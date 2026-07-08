import base64
import os
import re
import traceback
from pathlib import Path
from typing import Any, List, Tuple

from playwright.sync_api import Page
from config.constants import PHOTO_DIR
from config.logger_config import logger
from src.dashboard.runtime import StopRequested, capture_page_screenshot, emit_event
from src.job_manager.easy_applier import (
    BaseEasyApplier,
    EasyApplyLimitReached,
    NoInfoException,
)
from src.job_manager.resume_anonymizer import ResumeAnonymizer
from src.llm.llm_manager import GPTAnswerer
from src.pydantic_models.job_models import Job, Question
from src.utils.browser_utils import (
    debug_capture,
    find_element_safely,
    find_elements_safely,
    get_clean_text,
)
from src.utils.utils import (
    async_pause,
    get_ready_made_photo,
    get_ready_made_resume,
    load_yaml_file,
    sanitize_text,
)


class LinkedInEasyApplier(BaseEasyApplier):
    # Open-ended writing prompts we never auto-fill. Matched as case-insensitive
    # substrings against the (sanitized) field label. Covers motivational/essay
    # questions ("why do you want to work here", "tell us about yourself"),
    # cover letters, etc. Kept deliberately specific so short factual fields
    # (city, notice period, "how did you hear about us", profile URL) are NOT
    # caught and still get filled.
    WRITING_PROMPT_KEYWORDS = (
        "cover letter",
        "coverletter",
        "cover note",
        "summary",
        "additional information",
        "additional info",
        "additional comments",
        "anything else",
        "message to",
        "note to",
        "why do you want",
        "why would you",
        "why are you interested",
        "why do you wish",
        "why work",
        "why join",
        "why this company",
        "why this role",
        "why us",
        "what interests you",
        "what excites you",
        "what makes you",
        "what can you bring",
        "what are you looking for",
        "what motivates you",
        "tell us about",
        "tell us why",
        "tell me about",
        "describe your",
        "describe a time",
        "describe how",
        "describe why",
        "in your own words",
        "please elaborate",
        "please explain",
        "explain why",
        "motivation",
        "career goals",
        "career aspirations",
    )

    def __init__(
        self,
        page: Page,
        gpt_answerer: GPTAnswerer,
        resume_anonymizer: ResumeAnonymizer,
        resume_generator_manager,
        pause_checker,
        answers_file: Path,
        resume_dir: Path,
        cover_letter_dir: Path,
        test_mode: bool,
    ):
        logger.info("Initializing LinkedInEasyApplier")
        self.page = page
        self.gpt_answerer = gpt_answerer
        self.resume_anonymizer = resume_anonymizer
        self.resume_generator_manager = resume_generator_manager
        self.pause_checker = pause_checker
        self.answers_file = answers_file
        self.resume_dir = resume_dir
        self.generated_resume_dir = Path(resume_dir) / "generated_resumes"
        self.generated_photo_dir = Path(PHOTO_DIR)
        self.generated_cover_letter_dir = Path(cover_letter_dir) / "generated_cover_letters"
        self.ready_made_resume_path = get_ready_made_resume()
        self.ready_made_photo_path = get_ready_made_photo()
        self.all_questions = self._load_questions()
        self.current_job = None
        self.test_mode = test_mode
        self.previous_question_texts = []
        # Mirror BaseEasyApplier/Indeed: ensure the attribute always exists so
        # apply_to_job can return it even when no resume was uploaded this run.
        self.submitted_resume_path = None
        logger.info("LinkedInEasyApplier initialized successfully")

    def set_page(self, page: Page) -> None:
        self.page = page

    @staticmethod
    def _looks_like_cover_letter(text: str) -> bool:
        normalized = sanitize_text(text or "")
        return "cover letter" in normalized or "coverletter" in normalized

    @classmethod
    def _looks_like_writing_prompt(cls, text: str) -> bool:
        """True for open-ended/essay free-text prompts we never auto-write
        (cover letters, "why do you want to work here", "tell us about
        yourself", etc.). Short factual fields must NOT match."""
        normalized = sanitize_text(text or "")
        return any(keyword in normalized for keyword in cls.WRITING_PROMPT_KEYWORDS)

    @staticmethod
    def _context_text_is_required(text: str) -> bool:
        normalized = sanitize_text(text or "")
        if "optional" in normalized or "not required" in normalized:
            return False
        return "required" in normalized

    async def _field_is_required(
        self,
        section: Any,
        field: Any | None = None,
        context_text: str = "",
    ) -> bool:
        """Best-effort required-field detection for LinkedIn Easy Apply controls."""
        for element in (field, section):
            if not element:
                continue
            for attr in ("required", "aria-required", "data-required"):
                try:
                    value = await element.get_attribute(attr)
                except Exception:
                    value = None
                if isinstance(value, str) and value.lower() in {"true", "required"}:
                    return True
                if value is True:
                    return True

        text_candidates = [context_text]
        for element in (section,):
            if not element:
                continue
            try:
                text_candidates.append(await element.text_content() or "")
            except Exception:
                pass

        return any(self._context_text_is_required(text) for text in text_candidates)

    async def _clear_skipped_text_field(self, text_field: Any, question_text: str) -> None:
        """Clear skipped fields so stale browser/LinkedIn values are not submitted."""
        try:
            current_value = await text_field.get_attribute("value")
            if current_value:
                logger.info(f"Clearing skipped textbox: {question_text}")
            await text_field.fill("")
        except Exception as e:
            logger.warning(f"Failed to clear skipped textbox '{question_text}': {e}")

    @staticmethod
    def _easy_apply_surface_url(job: Job) -> str:
        """URL that serves LinkedIn's LEGACY Easy Apply modal.

        LinkedIn now serves a new React dialog (which this engine cannot read or
        fill) on the standalone /jobs/view/<id> page, but still serves the legacy
        modal from the search detail-pane surface (?currentJobId=<id>). Map the
        job's canonical URL to that surface; fall back to the original URL if no job
        id can be parsed.
        """
        match = re.search(r"/jobs/view/(\d+)", job.url or "") or re.search(
            r"currentJobId=(\d+)", job.url or ""
        )
        if not match:
            return job.url
        return f"https://www.linkedin.com/jobs/search/?currentJobId={match.group(1)}"

    async def check_for_premium_redirect(self, job: Job, max_attempts=3) -> bool:
        """Check for LinkedIn premium redirect and attempt to return to job page (async)"""
        current_url = self.page.url
        attempts = 0
        is_redirected = False
        while "linkedin.com/premium" in current_url and attempts < max_attempts:
            logger.warning("Redirected to linkedIn Premium page. Attempting to return to job page.")
            attempts += 1
            is_redirected = True
            await self.page.goto(self._easy_apply_surface_url(job))
            await async_pause(2, 3)
            current_url = self.page.url

        if "linkedin.com/premium" in current_url:
            logger.error(
                f"Failed to return to job page after {max_attempts} attempts. Cannot apply for the job."
            )
            raise Exception(
                f"Redirected to linkedIn Premium page and failed to return after {max_attempts} attempts. Job application aborted."
            )
        return is_redirected

    async def apply_to_job(self, job: Job) -> Tuple[Tuple[str, str], Any]:
        """
        Starts the process of applying to a job (async).
        :param job: A job object with the job details.
        :return: None
        """
        logger.info(f"Applying to job: {job.job_title} at {job.company_name}")
        emit_event(
            "easy_apply_started",
            f"Easy Apply started for {job.job_title}",
            job_title=job.job_title,
            company_name=job.company_name,
            url=job.url,
        )

        # Check for Easy Apply daily limit before attempting to apply
        if await self._check_easy_apply_limit():
            logger.warning("Easy Apply daily limit reached. Skipping job application.")
            return ("Limit", "Easy Apply daily limit reached. Skipping job application."), None

        try:
            apply_result = await self.job_easy_apply(job)
            return apply_result, self.submitted_resume_path
        except StopRequested:
            raise
        except Exception as e:
            logger.error(f"Failed to apply to job: {job.job_title} at {job.url}, error: {str(e)}")
            await debug_capture(self.page, "apply_to_job_error")
            raise e

    async def job_easy_apply(self, job: Job) -> Tuple[str, str]:
        """Main job application logic (async)"""
        try:
            self.current_job = job
            # Job scraping already happened on the standalone /jobs/view/<id> page,
            # but that page now opens the new React dialog this engine can't fill.
            # Switch the tab to the search detail-pane surface, which still serves the
            # legacy modal, before opening Easy Apply.
            surface_url = self._easy_apply_surface_url(job)
            if surface_url and surface_url != self.page.url:
                try:
                    await self.page.goto(surface_url, wait_until="domcontentloaded")
                    await async_pause(3, 4)
                    logger.info(f"Opened Easy Apply surface (legacy modal): {surface_url}")
                except Exception as e:
                    logger.warning(f"Could not switch to Easy Apply surface: {e}")
            try:
                await self.page.evaluate("document.activeElement && document.activeElement.blur()")
            except Exception:
                pass
            logger.debug("Focus removed from the active element")
            logger.info("Attempting to click 'Easy Apply' button")
            while True:
                # Click 'Easy Apply' button
                result = await self._find_easy_apply_button(job)
                if result is not True:
                    # _find_easy_apply_button returns None when it detected the daily
                    # limit, False when no button was found. Stop the whole run
                    # immediately if it's the daily limit; otherwise skip this job.
                    if result is None or await self._check_easy_apply_limit():
                        logger.warning(
                            "Easy Apply daily limit reached - stopping run immediately."
                        )
                        return ("Limit", "Easy Apply daily limit reached")
                    return (
                        "Skip",
                        "No clickable 'Easy Apply' button found, maybe you already applied to this job",
                    )
                logger.debug("'Easy Apply' button clicked successfully")
                await async_pause()
                # Click 'Continue Applying' button if it appears
                await self._click_continue_applying_button()
                await async_pause()
                # Check for premium redirect
                if not await self.check_for_premium_redirect(self.current_job):
                    break
                else:
                    logger.debug("Redirected to premium page, trying again")

            logger.info("Filling out application form")
            await async_pause(2, 3)
            await capture_page_screenshot(self.page, "easy-apply-opened")
            await self._fill_application_form(job)
            logger.info(f"Successfully applied to job: {job.job_title}")
            emit_event(
                "easy_apply_completed",
                f"Easy Apply completed for {job.job_title}",
                job_title=job.job_title,
                company_name=job.company_name,
                url=job.url,
            )
            return "Success", ""

        except NoInfoException as e:
            logger.warning(f"Could not apply to {job.job_title} at {job.company_name}. Reason: {e}")
            return (
                "Skip",
                f"Could not apply to {job.job_title} at {job.company_name}. Reason: {e}",
            )
        except EasyApplyLimitReached as e:
            logger.warning(f"Easy Apply daily limit reached - stopping run immediately. {e}")
            return ("Limit", "Easy Apply daily limit reached")
        except StopRequested:
            raise
        except Exception as exc:
            # An Easy Apply button that clicks but never opens a form modal almost
            # always means the job was already applied to (LinkedIn shows an
            # "Applied" state) or a transient UI hiccup — NOT a real failure. Treat
            # it as a Skip so the consecutive-failure circuit breaker doesn't abort
            # the whole run on already-applied jobs (history dedup is intentionally
            # off, so the bot legitimately revisits jobs it already applied to).
            if "modal content not found" in str(exc):
                # The modal also fails to open once the daily Easy Apply limit is
                # hit (LinkedIn shows the limit message instead of the form). Check
                # for that first and stop the whole run immediately if so; otherwise
                # it's an already-applied/transient case and we just skip the job.
                if await self._check_easy_apply_limit():
                    logger.warning(
                        "Easy Apply daily limit reached - stopping run immediately."
                    )
                    return ("Limit", "Easy Apply daily limit reached")
                logger.warning(
                    f"Easy Apply modal didn't open for {job.job_title} at "
                    f"{job.company_name} (likely already applied); skipping."
                )
                return (
                    "Skip",
                    "Easy Apply modal did not open (likely already applied)",
                )
            tb_str = traceback.format_exc()
            logger.error(f"Failed to apply to job: {job.job_title} at {job.url}, error: {tb_str}")
            await debug_capture(self.page, "job_easy_apply_error")
            await capture_page_screenshot(self.page, "easy-apply-error")
            try:
                await self._save_job_application_process()
            except Exception as e:
                logger.error(f"Failed to save job application process: {e}")
            return "Error", f"Failed to apply to job! Original exception:\nTraceback:\n{tb_str}"

    # Phrases that identify LinkedIn's "You reached today's Easy Apply limit"
    # modal/banner. Stored apostrophe-free and lowercase so matching survives
    # LinkedIn's typographic punctuation (e.g. "today's" with a U+2019 curly
    # apostrophe instead of a straight one) - see _normalize_limit_text.
    _EASY_APPLY_LIMIT_PHRASES = (
        "easy apply limit",
        "reached todays easy apply limit",
        "easy apply application limit",
        "application limit for today",
        "reached the daily limit",
        "we limit easy apply submissions",
        "daily submissions",
        "limit daily submissions",
        "continue applying tomorrow",
        "apply tomorrow",
    )

    @staticmethod
    def _normalize_limit_text(text: str) -> str:
        """Lowercase, drop apostrophe variants, and collapse whitespace.

        Makes limit-message matching robust to the typographic apostrophe
        LinkedIn uses in "today's" (U+2019), which would otherwise not match a
        phrase written with a straight apostrophe.
        """
        normalized = (text or "").lower()
        for apostrophe in ("'", "’", "‘", "ʼ", "`"):
            normalized = normalized.replace(apostrophe, "")
        return re.sub(r"\s+", " ", normalized).strip()

    async def _check_easy_apply_limit(self) -> bool:
        """Check if Easy Apply daily limit has been reached (async)

        Returns:
            bool: True if limit reached, False otherwise
        """
        logger.debug("Checking for Easy Apply daily limit")

        try:
            # Look for the error message indicating daily limit reached. Covers
            # both the inline form banner and the post-submit "Got it" modal.
            limit_error_selectors = [
                "//div[contains(@class, 'artdeco-inline-feedback--error')]//span[contains(@class, 'artdeco-inline-feedback__message')]",
                "//div[contains(@class, 'artdeco-inline-feedback--error')]",
                "//*[contains(@class, 'artdeco-modal')]//h2",
                "//*[contains(text(), 'Easy Apply') and contains(text(), 'limit')]",
                "//*[contains(text(), 'reached today')]",
                "//*[contains(text(), 'application limit')]",
                "//*[contains(text(), 'daily submissions')]",
                "//*[contains(text(), 'apply tomorrow')]",
            ]

            for selector in limit_error_selectors:
                try:
                    error_elements = await find_elements_safely(self.page, selector, "xpath")
                    if error_elements:
                        for element in error_elements:
                            normalized = self._normalize_limit_text(
                                await element.text_content() or ""
                            )
                            if normalized and any(
                                phrase in normalized
                                for phrase in self._EASY_APPLY_LIMIT_PHRASES
                            ):
                                logger.warning(f"Easy Apply daily limit reached: {normalized}")
                                return True
                except Exception as e:
                    logger.debug(f"Error checking selector {selector}: {e}")
                    continue

            return False

        except Exception as e:
            logger.warning(f"Error checking Easy Apply limit: {e}")
            await debug_capture(self.page, "easy_apply_limit_check_error")
            return False

    async def _find_easy_apply_button(self, job: Job) -> Any:
        """Find and click the job's Easy Apply button (async).

        LinkedIn's redesigned jobs surface uses obfuscated CSS class names, but the
        apply button keeps the stable id 'jobs-apply-button-id'
        (aria-label="Easy Apply to <title> at <company>"). Anchor on that, with an
        aria-label fallback. Both deliberately exclude the search-toolbar
        "Easy Apply filter." pill (id=searchFilter_applyWithLinkedin), which the old
        '//a[contains(., "Apply")]' selector could not distinguish — it matched a
        stray 'Apply' link, never opened the modal, and every job was then
        mislabeled "already applied".
        """
        logger.debug("Searching for 'Easy Apply' button and try to click")
        attempt = 0

        # Stable anchors for the real apply button, in priority order.
        easy_apply_selectors = [
            ("#jobs-apply-button-id", "css"),
            ("button[aria-label^='Easy Apply to']", "css"),
        ]

        while attempt < 2:
            await self.check_for_premium_redirect(job)

            # Check for Easy Apply limit before searching for button
            if await self._check_easy_apply_limit():
                logger.warning("Easy Apply daily limit detected while searching for button")
                return None

            for selector, by in easy_apply_selectors:
                easy_apply_buttons = await find_elements_safely(self.page, selector, by)
                for button in easy_apply_buttons:
                    try:
                        if not (await button.is_visible() and await button.is_enabled()):
                            logger.debug(f"Apply button ({selector}) not visible or enabled")
                            continue
                        await button.scroll_into_view_if_needed(timeout=2000)
                        await button.click(timeout=2000)
                        logger.debug(f"Clicked 'Easy Apply' button via selector: {selector}")
                        return True
                    except Exception as e:
                        logger.debug(f"Failed to click easy apply button ({selector}): {e}")

            await self.check_for_premium_redirect(job)

            if attempt == 0:
                logger.debug("Refreshing page to retry finding 'Easy Apply' button")
                await self.page.reload()
                await async_pause(3, 5)
            attempt += 1

        page_url = self.page.url
        logger.warning(
            f"No clickable 'Easy Apply' button found after 2 attempts. page url: {page_url}"
        )
        return False

    async def _click_continue_applying_button(self) -> None:
        """Click continue applying button if present (async)"""
        logger.debug("Searching for 'Continue Applying' button")
        continue_applying_button = await find_element_safely(
            self.page,
            '//*[contains(., "Continue applying") and (self::button or self::a)]',
            "xpath",
        )
        if continue_applying_button:
            await continue_applying_button.click(timeout=1000)

    async def _fill_application_form(self, job: Job):
        """Fill out application form with loop for multi-step forms (async)"""
        logger.info(f"Filling out application form for job: {job.job_title}")
        while True:
            self.previous_question_texts = []
            # Fill out application form
            await self._fill_up(job)
            # Check if execution is paused
            if self.pause_checker:
                await self.pause_checker()
            # Click 'Next' or 'Submit' or 'Confirm' button
            if await self._next_or_submit():
                logger.debug("Application form submitted")
                break

    async def _find_next_or_submit_button(self) -> Any:
        """Find 'Next' or 'Submit' or 'Review' button (async)"""
        logger.info("Finding 'Next' or 'Submit' or 'Review' button")
        # Find all elements with class="artdeco-button__text" and filter by specific text
        elements = await find_elements_safely(self.page, ".artdeco-button__text", "css")
        target_texts = ["next", "review", "submit application"]

        # Filter elements by text content
        next_button = None
        button_text = None

        for element in elements:
            text = await get_clean_text(element)
            if text.lower() in target_texts:
                next_button = element
                button_text = text.lower()
                break

        return next_button, button_text

    async def _next_or_submit(self) -> bool:
        """Click 'Next' or 'Submit' or 'Review' button"""
        logger.info("Clicking 'Next' or 'Submit' or 'Review' button")
        next_button, button_text = await self._find_next_or_submit_button()

        if next_button is None or button_text is None:
            logger.error("No 'Next' or 'Submit' button found on the page")
            raise Exception("Could not find 'Next' or 'Submit' button to proceed with application")

        if "submit application" in button_text:
            logger.debug("Submit button found, submitting application")
            await self._unfollow_company()
            await async_pause()
            if self.test_mode:
                logger.debug("Test mode is enabled, skipping application form submission")
                await self._discard_application()
                return True
            submitted = await self._check_and_fix_errors(next_button)
            # After clicking Submit, LinkedIn may show the daily limit modal
            # ("You reached today's Easy Apply limit") instead of confirming the
            # application. Detect it here so the run stops immediately rather than
            # counting this as a success and hammering Easy Apply on later jobs.
            if await self._check_easy_apply_limit():
                raise EasyApplyLimitReached(
                    "Easy Apply daily limit reached after submitting application"
                )
            return submitted
        await self._check_and_fix_errors(next_button)
        return False

    async def _unfollow_company(self) -> None:
        """Unfollow company checkbox (async)"""
        try:
            follow_checkbox = await find_element_safely(
                self.page,
                "label[for='follow-company-checkbox']",
                "css",
            )
            if follow_checkbox:
                await follow_checkbox.click(timeout=1000)

        except Exception as e:
            logger.warning(f"Failed to unfollow company: {e}")
            await debug_capture(self.page, "unfollow_company_error")

    async def _check_and_fix_errors(self, next_button: Any) -> bool:
        """Check for errors in the form and try to fix them (async)"""
        logger.info("Checking for errors in the form and trying to fix them")
        await async_pause(1, 2)
        await next_button.click(timeout=1000)
        await async_pause(2, 3)
        attempt = 0
        while attempt < 3:
            error_texts = await self._find_all_form_errors()
            if len(error_texts) > 0:
                logger.info(f"Found {len(error_texts)} errors")
                await self._fill_textbox_question_errors()
                await async_pause(1, 2)
                next_button, _ = await self._find_next_or_submit_button()
                try:
                    await next_button.click(timeout=1000)
                except Exception as e:
                    logger.warning(f"Failed to click next button: {e}")
                    await debug_capture(self.page, "next_button_click_error")
                await async_pause(2, 3)
            else:
                return True
            attempt += 1
        else:
            logger.error(f"Form submission failed with errors: {str(error_texts)}")
            raise Exception(
                f"Failed to answer questions or file upload with errors: {str(error_texts)}"
            )

    async def _discard_application(self) -> None:
        """Discard application (async)"""
        logger.info("Discarding application")
        try:
            dismiss = await find_element_safely(
                self.page, "//*[contains(@class, 'artdeco-modal__dismiss')]", "xpath"
            )
            if dismiss:
                await dismiss.click(timeout=1000)
                await async_pause(2, 3)
            confirm_buttons = self.page.locator(
                "xpath=//*[contains(@class, 'artdeco-modal__confirm-dialog-btn')]"
            )
            if await confirm_buttons.count() > 0:
                await confirm_buttons.first.click(timeout=1000)
                await async_pause(2, 3)
        except Exception as e:
            logger.warning(f"Failed to discard application: {e}")
            await debug_capture(self.page, "discard_application_error")

    async def _save_job_application_process(self) -> None:
        """Save job application process (async)"""
        logger.info("Application not completed. Saving job to My Jobs, In Progess section")
        try:
            dismiss = await find_element_safely(
                self.page, "//*[contains(@class, 'artdeco-modal__dismiss')]", "xpath"
            )
            if dismiss:
                await dismiss.click(timeout=1000)
                await async_pause(2, 3)
            confirm_buttons = self.page.locator(
                "xpath=//*[contains(@class, 'artdeco-modal__confirm-dialog-btn')]"
            )
            if await confirm_buttons.count() > 1:
                await confirm_buttons.nth(1).click(timeout=1000)
                await async_pause(2, 3)
        except Exception as e:
            logger.error(f"Failed to save application process: {e}")
            await debug_capture(self.page, "save_application_error")

    async def _fill_up(self, job: Job) -> None:
        """Fill up form sections (async)"""
        logger.info(f"Filling up form sections for job: {job.job_title}")

        try:
            # Wait for the Easy Apply modal content to be present with explicit wait
            modal_content = None
            logger.debug("Waiting for Easy Apply modal to appear...")

            # Try to wait for the modal to be visible
            try:
                # Wait up to 10 seconds for the modal to appear
                await self.page.wait_for_selector(
                    ".jobs-easy-apply-modal__content", state="visible", timeout=10000
                )
                logger.debug("Modal selector found via wait_for_selector")
            except Exception as e:
                logger.warning(f"wait_for_selector failed: {e}")

            # Try multiple selectors to find the modal content
            modal_selectors = [
                ".jobs-easy-apply-modal__content",  # CSS selector
                ".artdeco-modal__content",  # Fallback CSS
                "//*[contains(@class, 'jobs-easy-apply-modal__content')]",  # XPath
            ]

            for selector in modal_selectors:
                selector_type = (
                    "css" if selector.startswith(".") or selector.startswith("[") else "xpath"
                )
                modal_content = await find_element_safely(self.page, selector, selector_type)
                if modal_content is not None:
                    logger.debug(f"Easy Apply modal content found with selector: {selector}")
                    break

            if modal_content is None:
                logger.error("Easy Apply modal content not found on the page with any selector")
                raise Exception(
                    "Easy Apply modal content not found. The Easy Apply dialog may not be open."
                )

            logger.debug("Easy Apply modal content found successfully")

            # Track processed file inputs to avoid duplicate processing
            processed_file_inputs = set()

            # Find all form elements using the correct selectors
            form_elements = await modal_content.locator(".fb-dash-form-element").all()
            logger.debug(f"Found {len(form_elements)} form elements")

            if not form_elements:
                # Fallback to the old selector if new one doesn't work
                form_elements = await modal_content.locator(
                    "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]"
                ).all()
                logger.debug(
                    f"Fallback: Found {len(form_elements)} form elements with old selector"
                )

            # Process regular form elements
            for element in form_elements:
                try:
                    await self._process_form_element(element, job, processed_file_inputs)
                except NoInfoException:
                    raise

            # Also look for upload sections separately (they may not be in fb-dash-form-element)
            upload_sections = await modal_content.locator(
                ".js-jobs-document-upload__container"
            ).all()
            logger.debug(f"Found {len(upload_sections)} upload sections")

            for upload_section in upload_sections:
                logger.debug("Processing upload section")
                await self._handle_upload_fields(upload_section, job, processed_file_inputs)

            # Additional fallback: look for any file inputs that might be missed
            file_inputs = await modal_content.locator("input[type='file']").all()
            logger.debug(f"Found {len(file_inputs)} file inputs as additional check")

            for file_input in file_inputs:
                # Check if this file input was already processed
                file_input_id = await file_input.get_attribute("id") or str(id(file_input))
                if file_input_id not in processed_file_inputs:
                    logger.debug("Processing additional file input")
                    parent_container = file_input.locator("xpath=../..").first
                    await self._handle_upload_fields(parent_container, job, processed_file_inputs)
                    processed_file_inputs.add(file_input_id)
        except NoInfoException:
            raise
        except Exception as exc:
            tb_str = traceback.format_exc()
            logger.error(f"Failed to find form elements: {tb_str}")
            await debug_capture(self.page, "fill_up_form_error")
            # No modal at all almost always means the job was already applied to
            # (LinkedIn replaces the Easy Apply form with an "Applied" state). Re-raise
            # so job_easy_apply's skip-guard classifies it as a clean Skip instead of
            # pressing on to a phantom Next/Submit button and surfacing a generic error
            # that trips the consecutive-failure circuit breaker.
            if "modal content not found" in str(exc):
                raise
            # Any other (partial) failure: log it and continue filling what we can.
            logger.warning("Continuing without filling form elements due to error")

    async def _process_form_element(
        self, element: Any, job: Job, processed_file_inputs: set
    ) -> None:
        """Process form element (async)"""
        logger.debug("Processing form element")
        if await self._is_upload_field(element):
            await self._handle_upload_fields(element, job, processed_file_inputs)
        else:
            await self._process_form_section(element)

    async def _is_upload_field(self, element: Any) -> bool:
        """Check if element is upload field (async)"""
        # Check for file input elements
        file_inputs = await element.locator("xpath=.//input[@type='file']").all()

        # Also check for LinkedIn-specific upload containers
        upload_containers = await element.locator(".js-jobs-document-upload__container").all()
        upload_buttons = await element.locator(".jobs-document-upload__upload-button").all()

        is_upload = bool(file_inputs or upload_containers or upload_buttons)
        logger.debug(
            f"Element is upload field: {is_upload} (file_inputs: {len(file_inputs)}, containers: {len(upload_containers)}, buttons: {len(upload_buttons)})"
        )
        return is_upload

    async def _handle_upload_fields(
        self, element: Any, job: Job, processed_file_inputs: set
    ) -> None:
        """Handle file upload fields (async)"""
        logger.info("Handling upload fields")

        try:
            show_more_button = await find_element_safely(
                self.page,
                "//button[contains(@aria-label, 'Show more resumes')]",
                "xpath",
            )
            if show_more_button:
                await show_more_button.click(timeout=1000)
                logger.debug("Clicked 'Show more resumes' button")
        except Exception:
            logger.debug("'Show more resumes' button not found, continuing...")

        # First try to find file inputs within the specific element
        file_upload_elements = await element.locator("xpath=.//input[@type='file']").all()

        # If no file inputs found in the element, fall back to global search
        if not file_upload_elements:
            logger.debug("No file inputs found in element, searching globally")
            file_upload_elements = await self.page.locator("xpath=//input[@type='file']").all()

        logger.debug(f"Found {len(file_upload_elements)} file upload elements")

        for upload_element in file_upload_elements:
            try:
                # Check if this file input was already processed
                file_input_id = await upload_element.get_attribute("id") or str(id(upload_element))
                if file_input_id in processed_file_inputs:
                    logger.debug(f"File input {file_input_id} already processed, skipping")
                    continue

                # Get the parent container to determine what type of upload this is
                parent = upload_element.locator("xpath=..").first
                container_text = (await parent.text_content() or "").lower()

                # Also check the label text if available
                # try:
                #     input_id = await upload_element.get_attribute("id") or ""
                #     if input_id:
                #         label_text = (
                #             await self.page.locator(
                #                 f"xpath=//label[@for='{input_id}']"
                #             ).first.text_content()
                #             or ""
                #         ).lower()
                #         container_text += " " + label_text
                # except Exception:
                #     pass

                # Make the hidden input visible for uploading
                try:
                    await upload_element.evaluate("el => el.classList.remove('hidden')")
                except Exception:
                    pass

                logger.debug(f"Processing upload field with context: {container_text}")

                # Mark this file input as processed before generating files
                processed_file_inputs.add(file_input_id)

                accept_types = (await upload_element.get_attribute("accept") or "").lower()

                # output = self.gpt_answerer.resume_or_cover(container_text)
                if "image/" in accept_types or "photo" in container_text:
                    logger.info("Uploading photo")
                    await self._create_and_upload_photo(upload_element, job)
                elif "resume" in container_text:
                    if await self._detect_already_selected_resume(parent):
                        if self.ready_made_resume_path is not None:
                            self.submitted_resume_path = os.path.abspath(
                                str(self.ready_made_resume_path)
                            )
                        logger.info("Using LinkedIn's already selected resume")
                        continue
                    logger.info("Uploading resume")
                    await self._create_and_upload_resume(upload_element, job)
                elif "cover" in container_text:
                    logger.info("Skipping cover letter upload field")
                    continue

            except Exception as e:
                logger.warning(f"Failed to process upload element: {e}")
                await debug_capture(self.page, "upload_element_error")
                continue

        logger.debug("Finished handling upload fields")

    async def _create_and_upload_photo(self, element: Any, job: Job) -> None:
        """Upload a configured profile photo or fall back to the visible LinkedIn avatar."""
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif"}
        max_file_size = 2 * 1024 * 1024

        if self.ready_made_photo_path is not None:
            photo_path = self.ready_made_photo_path.resolve()
            if photo_path.suffix.lower() not in allowed_extensions:
                raise ValueError(
                    "Photo file format is not allowed. Only JPG, JPEG, PNG, and GIF formats are supported."
                )
            if photo_path.stat().st_size > max_file_size:
                raise ValueError("Photo file size exceeds the maximum limit of 2 MB.")

            abs_path = os.path.abspath(str(photo_path))
            await element.set_input_files(abs_path)
            await async_pause(1, 2)
            logger.info(f"Photo uploaded from path: {photo_path}")
            return

        os.makedirs(self.generated_photo_dir, exist_ok=True)

        image_selectors = [
            ".jobs-easy-apply-modal .artdeco-entity-lockup__image--type-circle img",
            ".jobs-easy-apply-modal img[src*='profile-displayphoto']",
            ".jobs-easy-apply-modal img[title][src]",
        ]

        image_src = ""
        for selector in image_selectors:
            image = await find_element_safely(self.page, selector, "css selector")
            if not image:
                continue
            image_src = (await image.get_attribute("src") or "").strip()
            if image_src:
                break

        if not image_src:
            raise ValueError("Could not locate a profile photo source for the upload field")

        data_url = await self.page.evaluate(
            """async (src) => {
                const response = await fetch(src, { credentials: 'include' });
                if (!response.ok) {
                    throw new Error(`Photo fetch failed with status ${response.status}`);
                }
                const blob = await response.blob();
                return await new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = () => reject(reader.error || new Error('Photo read failed'));
                    reader.readAsDataURL(blob);
                });
            }""",
            image_src,
        )

        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            raise ValueError("Profile photo fetch did not return an image data URL")

        header, encoded = data_url.split(",", 1)
        mime_type = header.split(";", 1)[0].split(":", 1)[1].lower()
        extension_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/gif": ".gif",
        }
        extension = extension_map.get(mime_type)
        if extension is None:
            raise ValueError(f"Unsupported photo MIME type for upload: {mime_type}")

        file_path = (
            self.generated_photo_dir / f"PHOTO_{job.company_name}_{job.job_title}{extension}"
        )
        file_bytes = base64.b64decode(encoded)
        if len(file_bytes) > max_file_size:
            raise ValueError("Profile photo exceeds LinkedIn 2 MB upload limit")

        with open(file_path, "wb") as file_handle:
            file_handle.write(file_bytes)

        await element.set_input_files(os.path.abspath(str(file_path)))
        await async_pause(1, 2)
        logger.info(f"Photo uploaded from generated path: {file_path}")

    async def _detect_already_selected_resume(self, parent: Any) -> bool:
        """Detect if the is already selected resume in Easy Apply form"""
        already_selected = False
        try:
            sel = ".jobs-document-upload-redesign-card__toggle-label"
            texts = await parent.locator(sel).evaluate_all(
                "els => els.map(e => e.textContent || '')"
            )
            if not texts:
                texts = await self.page.locator(sel).evaluate_all(
                    "els => els.map(e => e.textContent || '')"
                )
            for lbl_text in texts:
                lbl_text = lbl_text.strip()
                st = sanitize_text(lbl_text)
                if st.startswith("deselect") and st.endswith(".pdf"):
                    already_selected = True
                    logger.info(
                        f"Resume already selected via toggle label, skipping upload: {lbl_text}"
                    )
                    break
        except Exception:
            pass
        if already_selected:
            return True
        return False

    async def _create_and_upload_cover_letter(self, element: Any, job: Job) -> None:
        logger.info(
            "Cover letter generation and upload disabled; "
            f"skipping field for {job.job_title} at {job.company_name}"
        )

    async def _handle_terms_of_service(self, element: Any) -> bool:
        """Handle terms of service checkbox (async)"""
        try:
            checkboxes = await element.locator("input[type='checkbox']").all()
            if not checkboxes:
                return False
            checkbox_text = (
                await element.locator("xpath=.//label").first.text_content() or ""
            ).lower()
        except Exception:
            return False
        if checkbox_text:
            if any(
                term in checkbox_text
                for term in [
                    "terms of service",
                    "privacy policy",
                    "terms of use",
                    "confirm",
                    "agree",
                    "accept",
                ]
            ):
                label_el = element.locator("xpath=.//label").first
                await label_el.click(timeout=1000)
                logger.debug("Clicked terms of service checkbox/radio")
                return True
        return False

    async def _find_and_handle_checkbox_question(self, section: Any) -> bool:
        """Handle checkbox questions that are not terms of service (async)"""
        logger.debug("Searching for checkbox questions in the section.")

        # Look for checkboxes in the new LinkedIn form structure
        checkboxes = {}

        # Try different selectors for checkboxes
        checkbox_selectors = [
            "input[type='checkbox']",
            ".fb-form-element__checkbox",
            # "[data-test-text-selectable-option__input]",
            "[data-test-checkbox-form-component] input[type='checkbox']",
        ]

        for selector in checkbox_selectors:
            found_checkboxes = await find_elements_safely(section, selector, "css selector")
            for checkbox in found_checkboxes:
                checkbox_id = await checkbox.get_attribute("id")
                if checkbox_id and checkbox_id not in checkboxes:
                    checkboxes[checkbox_id] = checkbox

        checkboxes = list(checkboxes.values())

        if checkboxes:
            logger.debug(f"Found {len(checkboxes)} checkboxes")

            # Extract question text from the section
            question_text = ""
            try:
                # Look for question text in various places
                question_selectors = [
                    "legend",
                    ".fb-dash-form-element__label",
                    "[data-test-checkbox-form-title]",
                    ".jobs-easy-apply-form-section__group-title",
                ]

                for selector in question_selectors:
                    question_element = await find_element_safely(section, selector, "css selector")
                    if question_element:
                        question_text = (await question_element.text_content() or "").strip()
                        # Clean up the question text
                        question_text = self._deduplicate_question_text(question_text)
                        question_list = []
                        for question in question_text.split("\n"):
                            question_text = self._deduplicate_question_text(question.strip())
                            if question_text:
                                question_list.append(question_text)
                        question_text = "\n".join(question_list)
                        if question_text and "required" not in question_text.lower():
                            break

                # If no question found, try to get it from the section text
                if not question_text:
                    section_text = (await section.text_content() or "").strip()
                    # Extract meaningful text, removing checkbox labels
                    lines = section_text.split("\n")
                    for line in lines:
                        line = line.strip()
                        if line and not any(
                            opt in line.lower()
                            for opt in ["confirmed", "agree", "accept", "required"]
                        ):
                            question_text = line
                            break

                logger.debug(f"Extracted question text: '{question_text}'")
                self.previous_question_texts.append(question_text)

            except Exception as e:
                logger.warning(f"Failed to extract question text: {e}")
                question_text = ""

            # Extract checkbox options
            checkbox_options = []
            checkbox_data = []  # Store (checkbox, label_text) pairs

            for checkbox in checkboxes:
                try:
                    # Get the associated label
                    checkbox_id = await checkbox.get_attribute("id")
                    label_text = ""

                    if checkbox_id:
                        # Look for label with matching 'for' attribute
                        label = section.locator(f"label[for='{checkbox_id}']").first
                        if label:
                            label_text = (await label.text_content() or "").strip()

                    # If no label found, try to get text from parent elements
                    if not label_text:
                        # Look for text in the same container as the checkbox
                        parent = checkbox.locator("xpath=..").first
                        if parent:
                            parent_text = (await parent.text_content() or "").strip()
                            # Extract text that's not the question
                            if parent_text and parent_text != question_text:
                                label_text = parent_text

                    if label_text:
                        checkbox_options.append(label_text)
                        checkbox_data.append((checkbox, label_text))
                        logger.debug(f"Checkbox option: '{label_text}'")

                except Exception as e:
                    logger.warning(f"Failed to extract checkbox option: {e}")
                    continue

            if not checkbox_options:
                logger.debug("No checkbox options found, skipping")
                return False

            # Use LLM to select which checkboxes to check
            try:
                logger.info(f"Asking LLM to select checkboxes for question: {question_text}")
                logger.info(f"Available options: {checkbox_options}")

                # Always answer "Yes" to commute/relocation-willingness
                # questions — a "No" auto-rejects the application. Runs before
                # the cache so a stale "No" can never be reused.
                forced_yes = None
                if self._is_commute_relocation_question(question_text):
                    forced_yes = self._pick_yes_option(checkbox_options)

                # Look for existing answer if it's not a cover letter field
                existing_answer = None
                current_question_sanitized = sanitize_text(question_text)
                for item in self.all_questions:
                    if (
                        item.question == current_question_sanitized
                        and item.question_type == "checkbox"
                    ):
                        existing_answer = item.answer
                        logger.debug(f"Found existing answer: {existing_answer}")
                        break

                if forced_yes is not None:
                    selected_options = [forced_yes]
                    logger.info(
                        f"Commute/relocation question detected; forcing 'Yes' "
                        f"(option='{forced_yes}') for: {question_text}"
                    )
                    self._save_questions(
                        Question(
                            question_type="checkbox",
                            question=question_text,
                            answer=selected_options,
                        )
                    )
                elif existing_answer:
                    selected_options = existing_answer
                else:
                    selected_options = self.gpt_answerer.select_many_answers_from_options(
                        question_text, checkbox_options, self.previous_question_texts[:-1]
                    )
                    if not any(s.lower().startswith("no info") for s in selected_options):
                        self._save_questions(
                            Question(
                                question_type="checkbox",
                                question=question_text,
                                answer=selected_options,
                            )
                        )

                logger.info(f"LLM selected options: {selected_options}")

                # Check the selected checkboxes
                for checkbox, label_text in checkbox_data:
                    try:
                        # Check if this option was selected by LLM
                        if any(
                            selected in label_text.lower() or label_text.lower() in selected.lower()
                            for selected in selected_options
                            if not selected.lower().startswith("no info")
                        ):
                            if not await checkbox.is_checked():
                                logger.info(f"Checking checkbox: {label_text}")
                                await self._click_checkbox_safely(checkbox, section)
                                logger.debug(f"Clicked checkbox: {label_text}")
                            else:
                                logger.debug(f"Checkbox already checked: {label_text}")
                        else:
                            logger.debug(f"Checkbox not selected by LLM: {label_text}")

                    except Exception as e:
                        logger.warning(f"Failed to process checkbox '{label_text}': {e}")
                        continue

                return True

            except Exception as e:
                logger.error(f"Failed to use LLM for checkbox selection: {e}")
                # Fallback: check confirmation checkboxes only
                for checkbox, label_text in checkbox_data:
                    try:
                        if any(
                            confirm_word in label_text.lower()
                            for confirm_word in ["confirmed", "confirm", "agree", "accept"]
                        ):
                            if not await checkbox.is_checked():
                                logger.info(
                                    f"Fallback: Checking confirmation checkbox: {label_text}"
                                )
                                await self._click_checkbox_safely(checkbox, section)
                                logger.debug(f"Clicked confirmation checkbox: {label_text}")
                    except Exception as e:
                        logger.warning(f"Failed to process fallback checkbox '{label_text}': {e}")
                        continue

                return True

        return False

    async def _click_checkbox_safely(self, checkbox: Any, section: Any) -> None:
        """Safely click a checkbox by trying the label first, then the checkbox itself (async)"""
        try:
            # First try to click the associated label
            checkbox_id = await checkbox.get_attribute("id")
            if checkbox_id:
                label = section.locator(f"label[for='{checkbox_id}']").first
                if label:
                    logger.debug("Clicking checkbox via label")
                    await label.click(timeout=1000)
                    return

            # If no label found or label click failed, try clicking the checkbox directly
            logger.debug("Clicking checkbox directly")
            await checkbox.click(timeout=1000)

        except Exception as e:
            logger.warning(f"Failed to click checkbox safely: {e}")
            # Final fallback: try clicking the checkbox directly
            try:
                await checkbox.click(timeout=1000)
            except Exception as e2:
                logger.error(f"All checkbox click attempts failed: {e2}")
                await debug_capture(self.page, "checkbox_click_error")

    async def _find_and_handle_radio_question(self, section: Any) -> bool:
        """Handle radio button questions (async)"""
        # Look for radio buttons in the new LinkedIn form structure
        logger.debug("Searching for radio buttons in the section.")
        radios = {}

        # Try different selectors for radio buttons
        radio_selectors = [
            ".fb-text-selectable__option",
            "input[type='radio']",
            ".artdeco-button--toggle",
            "[role='radio']",
        ]

        for selector in radio_selectors:
            loc = section.locator(selector)
            ids = await loc.evaluate_all("els => els.map(e => e.id || '')")
            found_radios = await loc.all()
            for radio, radio_id in zip(found_radios, ids):
                if radio_id and radio_id not in radios:
                    radios[radio_id] = radio

        # Remove duplicates
        radios = list(radios.values())

        if radios:
            # Try to find the question text
            try:
                question_text = (await section.text_content() or "").lower().strip()
                question_list = []
                for question in question_text.split("\n"):
                    question_text = self._deduplicate_question_text(question.strip())
                    if question_text:
                        question_list.append(question_text)
                question_text = "\n".join(question_list)
                question_text = self._deduplicate_question_text(question_text)
                self.previous_question_texts.append(question_text)
            except Exception:
                question_text = ""

            # Extract options text from radio buttons and their labels
            options = await section.locator(",".join(radio_selectors)).evaluate_all(
                """els => {
                    const seen = new Set();
                    return els.reduce((acc, e) => {
                        if (e.id && !seen.has(e.id)) {
                            seen.add(e.id);
                            const lbl = document.querySelector('label[for="' + e.id + '"]');
                            const text = (lbl?.textContent || '').trim().toLowerCase();
                            if (text) acc.push(text);
                        }
                        return acc;
                    }, []);
                }"""
            )
            options = list(dict.fromkeys(options))

            if not options:
                logger.debug("No options extracted from radio buttons, skipping")
                return False

            # Never let the LLM (or a stale cache) answer a "do you require visa
            # sponsorship" question — a "Yes" there auto-rejects the application.
            if self._is_sponsorship_requirement_question(question_text):
                no_option = self._pick_no_option(options)
                if no_option is not None:
                    logger.info(
                        f"Sponsorship-requirement question detected; forcing "
                        f"'No' (option='{no_option}') for: {question_text}"
                    )
                    question_data = Question(
                        question_type="radio", question=question_text, answer=no_option
                    )
                    self._save_questions(question_data)
                    self.all_questions = self._load_questions()
                    await self._select_radio(section, radios, no_option)
                    return True
                logger.warning(
                    f"Sponsorship question detected but no 'No' option in {options}; "
                    "falling back to normal handling"
                )

            # Always answer "Yes" to commute/relocation-willingness questions —
            # a "No" auto-rejects the application. Runs before the cache so a
            # stale "No" can never be reused.
            if self._is_commute_relocation_question(question_text):
                yes_option = self._pick_yes_option(options)
                if yes_option is not None:
                    logger.info(
                        f"Commute/relocation question detected; forcing "
                        f"'Yes' (option='{yes_option}') for: {question_text}"
                    )
                    question_data = Question(
                        question_type="radio", question=question_text, answer=yes_option
                    )
                    self._save_questions(question_data)
                    self.all_questions = self._load_questions()
                    await self._select_radio(section, radios, yes_option)
                    return True
                logger.warning(
                    f"Commute/relocation question detected but no 'Yes' option in "
                    f"{options}; falling back to normal handling"
                )

            cached_question = self._find_cached_question(question_text, "radio")
            if cached_question:
                await self._select_radio(section, radios, cached_question.answer)
                logger.debug("Selected existing radio answer")
                return True

            logger.info(f"Asking question: {question_text}")
            logger.info(f"Available options: {options}")
            answer = self.gpt_answerer.select_one_answer_from_options(
                question_text, options, self.previous_question_texts[:-1]
            )
            if answer.lower().startswith("no info"):
                raise NoInfoException(f"No info found for question: {question_text}")
            question_data = Question(question_type="radio", question=question_text, answer=answer)
            self._save_questions(question_data)
            self.all_questions = self._load_questions()
            await self._select_radio(section, radios, answer)
            logger.debug("Selected new radio answer")
            return True
        return False

    async def _find_and_handle_textbox_question(self, section: Any) -> bool:
        """Handle textbox questions (async)"""
        logger.debug("Searching for text fields in the section.")

        # Look for text input fields in the new LinkedIn form structure
        text_field = None

        # Try different selectors for text inputs
        selectors = [
            "input[type='text']",
            "textarea",
            ".artdeco-text-input--input",
        ]

        for selector in selectors:
            fields = await find_elements_safely(section, selector, "css selector")
            if fields:
                text_field = fields[0]
                break

        if text_field:
            # Try to find the label for this field
            try:
                # Look for label in various ways
                label = None
                label_selectors = [
                    "label",
                    ".fb-dash-form-element__label",
                    ".artdeco-text-input--label",
                    ".jobs-easy-apply-form-section__group-title",
                ]

                for label_selector in label_selectors:
                    label = await find_element_safely(section, label_selector, "css selector")
                    if label:
                        break

                if label:
                    question_text = (await label.text_content() or "").lower().strip()
                    question_text = self._deduplicate_question_text(question_text)
                else:
                    question_text = ""

                # Add placeholder to question text if it exists
                placeholder_text = await text_field.get_attribute(
                    "placeholder"
                ) or await text_field.get_attribute("aria-label")
                if placeholder_text:
                    placeholder_text = placeholder_text.lower().strip()
                    placeholder_text = self._deduplicate_question_text(placeholder_text)
                    question_text += f"\n{placeholder_text}"

                self.previous_question_texts.append(question_text)
                logger.debug(f"Found text field with label: {question_text}")
            except Exception as e:
                logger.warning(f"Could not find label for text field: {e}")
                question_text = ""

            is_numeric = await self._is_numeric_field(text_field, question_text)
            logger.info(f"Is the field numeric? {'Yes' if is_numeric else 'No'}")

            question_type = "numeric" if is_numeric else "textbox"
            is_salary_expectation = (
                is_numeric and self._looks_like_salary_expectation_question(question_text)
            )
            is_location = self._looks_like_location_question(question_text)
            # Always answer "Yes" to commute/relocation-willingness questions —
            # a "No" auto-rejects the application. Only yes/no-phrased,
            # non-numeric questions qualify; runs before the cache so a stale
            # "No" can never be reused.
            is_commute_yes = (
                not is_numeric
                and self._is_commute_relocation_question(question_text)
                and self._looks_like_yes_no_phrasing(question_text)
            )

            # Check if it's a cover letter field (case-insensitive)
            is_cover_letter = self._looks_like_cover_letter(question_text)
            is_required_textbox = await self._field_is_required(
                section, text_field, question_text
            )
            # Open-ended writing prompts (cover letters, "why do you want to work
            # here", "tell us about yourself", etc.) are never auto-written.
            # Mandatory -> skip the whole job immediately (no wasted retries on a
            # field we will never fill); optional -> leave it blank and continue.
            # NoInfoException is caught upstream and recorded as a clean Skip (not
            # an Error). Numeric fields are never writing prompts.
            if not is_numeric and self._looks_like_writing_prompt(question_text):
                if is_required_textbox:
                    logger.info(
                        f"Mandatory free-text/essay question - skipping job: {question_text}"
                    )
                    raise NoInfoException(
                        f"Mandatory free-text/essay question requires writing: {question_text}"
                    )
                await self._clear_skipped_text_field(text_field, question_text)
                logger.info(
                    f"Leaving optional free-text/essay question blank: {question_text}"
                )
                return True
            logger.info(f"question: {question_text}")
            # Look for existing answer if it's not a cover letter field
            existing_answer = None
            if (
                not is_cover_letter
                and not is_salary_expectation
                and not is_location
                and not is_commute_yes
            ):
                cached_question = self._find_cached_question(question_text, question_type)
                if cached_question:
                    existing_answer = cached_question.answer.strip()
                    logger.debug(
                        "Found existing answer for '%s' via cached %s field",
                        question_text,
                        cached_question.question_type,
                    )

            if is_commute_yes:
                answer = "Yes"
                logger.info(
                    f"Commute/relocation question detected; forcing 'Yes' for: {question_text}"
                )
            elif existing_answer:
                answer = existing_answer
                logger.info(f"Using existing answer: {answer}")
            elif is_salary_expectation:
                answer = self._salary_expectation_answer(
                    is_numeric, is_hourly=self._looks_like_hourly_question(question_text)
                )
                logger.info(
                    "Using salary expectation for '%s': %s",
                    question_text,
                    answer,
                )
            elif is_location:
                answer = self._location_answer(question_text)
                logger.info("Using location for '%s': %s", question_text, answer)
            else:
                if is_numeric:
                    logger.info(f"Answering numeric question: {question_text}")
                    answer = self.gpt_answerer.answer_question_numeric(
                        question_text, self.previous_question_texts[:-1]
                    )
                else:
                    logger.info(f"Answering textual question: {question_text}")
                    answer = self.gpt_answerer.answer_question_textual_wide_range(
                        question_text, self.previous_question_texts[:-1]
                    )

            if answer.lower().startswith("no info"):
                raise NoInfoException(f"No info found for question: {question_text}")

            answer = self.resume_anonymizer.deanonymize_text(answer)
            await text_field.fill(answer)
            logger.debug("Entered answer into the textbox.")

            await self._process_autocomplete_suggestions(text_field)

            # Save non-cover letter answers
            if not is_cover_letter and not existing_answer:
                question_data = Question(
                    question_type=question_type, question=question_text, answer=answer
                )
                self._save_questions(question_data)
                logger.debug("Saved non-cover letter answer.")

            return True

        logger.debug("No text fields found in the section.")
        return False

    async def _process_autocomplete_suggestions(self, text_field: Any) -> None:
        """Handle autocomplete suggestions if they appear (async)"""
        await async_pause(1, 2)
        try:
            # Check if autocomplete suggestions are visible
            suggestions = self.page.locator(".basic-typeahead__selectable")
            if await suggestions.count() > 0:
                logger.debug("Autocomplete suggestions detected, selecting first option")
                try:
                    await self.page.keyboard.press("ArrowDown")
                    await async_pause()
                    await self.page.keyboard.press("Enter")
                except Exception:
                    pass
                logger.debug("Selected first suggestion from autocomplete")
        except Exception as e:
            logger.debug(f"No autocomplete suggestions found or error handling suggestions: {e}")

    async def _find_and_handle_dropdown_question(self, section: Any) -> bool:
        """Handle dropdown questions (async)"""
        try:
            # Look for dropdowns in the new LinkedIn form structure
            dropdowns = {}

            # Try different selectors for dropdowns
            dropdown_selectors = [
                "select",
                "[data-test-text-entity-list-form-select]",
                ".fb-dash-form-element__select-dropdown",
                "select.fb-dash-form-element__select-dropdown",
            ]

            for selector in dropdown_selectors:
                _selector = f"css={selector}" if selector == "select" else selector
                found_dropdowns = await find_elements_safely(section, _selector, "css selector")
                for dropdown in found_dropdowns:
                    dropdown_id = await dropdown.get_attribute("id")
                    if dropdown_id and dropdown_id not in dropdowns:
                        dropdowns[dropdown_id] = dropdown

            # Remove duplicates
            dropdowns = list(dropdowns.values())

            if dropdowns:
                logger.info("Dropdowns found")
                dropdown = dropdowns[0]
                # Try to gather options text if possible
                options = []
                try:
                    # For native select elements, get options via DOM
                    options = [
                        t
                        for t in await dropdown.locator("option").evaluate_all(
                            "els => els.map(e => e.textContent?.trim() ?? '')"
                        )
                        if t
                    ]
                except Exception:
                    options = []

                # Try to find the label for this dropdown
                try:
                    label_selectors = [
                        "label",
                        ".fb-dash-form-element__label",
                        "[data-test-text-entity-list-form-title]",
                    ]

                    question_text = ""
                    for label_selector in label_selectors:
                        label = await find_element_safely(section, label_selector, "css selector")
                        if label:
                            question_text = (await label.text_content() or "").lower().strip()
                            question_text = self._deduplicate_question_text(question_text)
                            self.previous_question_texts.append(question_text)
                            break
                except Exception as e:
                    logger.warning(f"Could not find label for dropdown: {e}")
                    question_text = ""

                # Never let the LLM (or a stale cache / prefilled value) answer a
                # "do you require visa sponsorship" question — a "Yes" there
                # auto-rejects the application.
                if self._is_sponsorship_requirement_question(question_text):
                    no_option = self._pick_no_option(options)
                    if no_option is not None:
                        logger.info(
                            f"Sponsorship-requirement question detected; forcing "
                            f"'No' (option='{no_option}') for: {question_text}"
                        )
                        self._save_questions(
                            Question(
                                question_type="dropdown",
                                question=question_text,
                                answer=no_option,
                            )
                        )
                        await self._select_dropdown_option(dropdown, no_option)
                        return True
                    logger.warning(
                        f"Sponsorship question detected but no 'No' option in {options}; "
                        "falling back to normal handling"
                    )

                # Always answer "Yes" to commute/relocation-willingness
                # questions — a "No" auto-rejects the application. Runs before
                # the prefilled/cached checks so a stale or prefilled "No" can
                # never be reused.
                if self._is_commute_relocation_question(question_text):
                    yes_option = self._pick_yes_option(options)
                    if yes_option is not None:
                        logger.info(
                            f"Commute/relocation question detected; forcing "
                            f"'Yes' (option='{yes_option}') for: {question_text}"
                        )
                        self._save_questions(
                            Question(
                                question_type="dropdown",
                                question=question_text,
                                answer=yes_option,
                            )
                        )
                        await self._select_dropdown_option(dropdown, yes_option)
                        return True
                    logger.warning(
                        f"Commute/relocation question detected but no 'Yes' option in "
                        f"{options}; falling back to normal handling"
                    )

                try:
                    current_selection = (
                        await dropdown.locator("option:checked").first.text_content() or ""
                    ).strip()
                except Exception:
                    current_selection = ""
                logger.debug(f"Current selection: {current_selection}")

                if self._is_meaningful_existing_answer(current_selection):
                    logger.info(
                        f"Dropdown question '{question_text}' already has a selected answer: {current_selection}"
                    )
                    self._save_questions(
                        Question(
                            question_type="dropdown",
                            question=question_text,
                            answer=current_selection,
                        )
                    )
                    return True

                existing_answer = None
                cached_question = self._find_cached_question(question_text, "dropdown")
                if cached_question:
                    existing_answer = (
                        cached_question.answer.strip()
                        if isinstance(cached_question.answer, str)
                        else cached_question.answer
                    )

                if existing_answer:
                    logger.debug(
                        f"Found existing answer for question '{question_text}': {existing_answer}"
                    )
                    if current_selection != existing_answer:
                        logger.debug(f"Updating selection to: {existing_answer}")
                        await self._select_dropdown_option(dropdown, existing_answer)
                else:
                    logger.info(f"Asking question: {question_text}")
                    logger.info(f"Available options: {options}")
                    answer = self.gpt_answerer.select_one_answer_from_options(
                        question_text, options, self.previous_question_texts[:-1]
                    )
                    if answer.lower().startswith("no info"):
                        raise NoInfoException(f"No info found for question: {question_text}")
                    question_data = Question(
                        question_type="dropdown", question=question_text, answer=answer
                    )
                    self._save_questions(question_data)
                    await self._select_dropdown_option(dropdown, answer)
                    logger.debug(f"Selected new dropdown answer: {answer}")

                return True

            else:
                logger.debug("No dropdown found. Logging elements for debugging.")
                try:
                    elements_count = await section.locator("xpath=.//*").count()
                    logger.debug(f"Elements found count: {elements_count}")
                except Exception:
                    pass
                return False

        except NoInfoException:
            raise
        except Exception as e:
            logger.warning(f"Failed to handle dropdown or combobox question: {e}", exc_info=True)
            await debug_capture(self.page, "dropdown_question_error")
            return False

    _NUMERIC_QUESTION_KEYWORDS = (
        "salary",
        "compensation",
        "pay",
        "wage",
        "rate",
        "earnings",
        "years of experience",
        "how many years",
        "how many months",
        "number of",
        "how many",
        "gpa",
        "grade point",
    )

    async def _is_numeric_field(self, field: Any, question_text: str = "") -> bool:
        """Check if field is numeric (async)"""
        field_type = (await field.get_attribute("type") or "").lower()
        field_id = (await field.get_attribute("id") or "").lower()
        is_numeric = (
            "numeric" in field_id
            or field_type == "number"
            or ("text" == field_type and "numeric" in field_id)
        )
        if not is_numeric:
            q = question_text.lower()
            is_numeric = any(
                re.search(r"\b" + re.escape(kw) + r"\b", q)
                for kw in self._NUMERIC_QUESTION_KEYWORDS
            )
        logger.debug(f"Field type: {field_type}, Field ID: {field_id}, Is numeric: {is_numeric}")
        return is_numeric

    def _is_meaningful_existing_answer(self, value: str | None) -> bool:
        if not value:
            return False
        normalized = sanitize_text(value)
        if not normalized:
            return False
        placeholders = {
            "select an option",
            "choose an option",
            "select",
            "choose",
            "please select",
            "empty response",
            "no info",
        }
        return normalized not in placeholders

    def _deduplicate_question_text(self, question_text: str) -> str:
        """If the question consists of two lines, and the second line is the same as the first line, use the first line"""
        if len(question_text) % 2 == 0:
            half_length = len(question_text) // 2
            if question_text[:half_length] == question_text[half_length:]:
                question_text = question_text[:half_length]
                return question_text
        question_list = question_text.split("\n")
        deduplicated_question_list = list(dict.fromkeys(question_list))
        question_text = "\n".join(deduplicated_question_list)
        return question_text

    async def _select_radio(self, section: Any, radios: List[Any], answer: str) -> None:
        """Select radio button based on answer (async)"""
        logger.debug(f"Selecting radio option: {answer}")
        for radio in radios:
            try:
                # Extract text from radio button or its associated label
                radio_text = ""

                # Look for label with matching 'for' attribute
                radio_id = await radio.get_attribute("id")
                if radio_id:
                    label = section.locator(f"label[for='{radio_id}']").first
                    radio_text = (await label.text_content() or "").strip().lower()

                logger.debug(f"Radio button text extracted: '{radio_text}'")

                if radio_text and (answer.lower() in radio_text or radio_text in answer.lower()):
                    # Try different ways to click the radio button
                    try:
                        # First try clicking the associated label (most reliable for LinkedIn)
                        if radio_id:
                            await label.click(timeout=1000)
                            logger.debug(f"Clicked radio label: {radio_text}")
                            return
                    except Exception:
                        logger.warning(f"Failed to click radio button: {radio_text}")

            except Exception as e:
                logger.warning(f"Failed to process radio button: {e}")
                continue

        # If no match found, click the first radio button as fallback
        try:
            await radios[0].click(timeout=1000)
            logger.debug("Clicked first radio button as fallback")
        except Exception:
            logger.warning("Failed to click any radio button")

    async def _select_dropdown_option(self, element: Any, text: str) -> None:
        """Select dropdown option by visible text using robust matching (async).

        Tries exact label match, then normalized label (collapsed whitespace),
        then label without parenthetical (e.g., removes "(+1)"), and finally
        resolves to the option's 'value' when a label match is found.
        """
        logger.debug(f"Selecting dropdown option: {text}")

        def normalize_label(s: str) -> str:
            try:
                import re as _re

                return _re.sub(r"\s+", " ", s or "").strip().lower()
            except Exception:
                return (s or "").strip().lower()

        label_candidates = []
        original = (text or "").strip()
        label_candidates.append(original)

        # Collapsed whitespace
        collapsed = " ".join(original.split())
        if collapsed not in label_candidates:
            label_candidates.append(collapsed)

        # Remove parenthetical like "(+1)"
        if "(" in original and ")" in original:
            base = original.split("(")[0].strip()
            if base and base not in label_candidates:
                label_candidates.append(base)

        # Normalize candidates for matching
        normalized_candidates = [normalize_label(c) for c in label_candidates]

        # First try direct label selection with primary candidate
        try:
            await element.select_option(label=label_candidates[0], timeout=3000)
            return
        except Exception as e:
            logger.warning(f"Failed to select dropdown option '{text}': {e}")
            pass

        # Inspect available <option> elements to resolve the correct value
        try:
            matched_value = None
            opts_data = await element.locator("option").evaluate_all(
                "els => els.map(e => ({label: e.textContent || '', value: e.value || ''}))"
            )
            norm_to_value = [
                (normalize_label(d["label"]), d["value"]) for d in opts_data if d["label"].strip()
            ]

            # Exact match on normalized labels
            for cand in normalized_candidates:
                for opt_label_norm, opt_value in norm_to_value:
                    if opt_label_norm == cand:
                        matched_value = opt_value
                        break
                if matched_value:
                    break

            # Contains match if no exact
            if not matched_value:
                for cand in normalized_candidates:
                    for opt_label_norm, opt_value in norm_to_value:
                        if cand and cand in opt_label_norm:
                            matched_value = opt_value
                            break
                    if matched_value:
                        break

            if matched_value:
                await element.select_option(value=matched_value)
                return
        except Exception as e:
            logger.warning(f"Failed to select dropdown option '{text}': {e}")
            pass

        # Final fallbacks: try label with collapsed whitespace, then value
        for cand in label_candidates[1:]:
            try:
                await element.select_option(label=cand)
                return
            except Exception:
                continue

        try:
            await element.select_option(value=collapsed)
            return
        except Exception as e:
            logger.warning(f"Failed to select dropdown option '{text}': {e}")

    async def _find_all_form_errors(self) -> List[str]:
        error_selectors = [
            ".artdeco-inline-feedback--error .artdeco-inline-feedback__message",
            ".artdeco-inline-feedback--error",
            "[role='alert'][data-test-form-element-error-messages]",
        ]
        errors_text: List[str] = []
        seen: set[str] = set()
        for selector in error_selectors:
            try:
                texts = await self.page.locator(selector).evaluate_all(
                    "els => els.map(e => e.textContent?.trim() || '')"
                )
                for txt in texts:
                    if txt and txt not in seen:
                        seen.add(txt)
                        errors_text.append(txt)
            except Exception as e:
                logger.debug(f"Failed to locate error elements with '{selector}': {e}")
        return errors_text

    async def _find_textbox_question_errors(self) -> List[Tuple[Any, str, str]]:
        """Find textbox fields that have validation errors and return details (async).

        Returns:
            List of tuples for each errored textbox field:
            - WebElement: the textbox (or textarea) element that needs correction
            - str: the question/label text associated with the field
            - str: the visible error message text
        """
        logger.debug("Searching for textbox validation errors in the Easy Apply modal")
        results: List[Tuple[Any, str, str]] = []

        form_container_selectors = [
            "xpath=.//*[contains(@class, 'fb-dash-form-element')]",
            "div[data-test-form-element]",
            "[data-test-single-line-text-form-component]",
            "[data-test-multiline-text-form-component]",
            "xpath=.//*[contains(@class, 'jobs-easy-apply-form-section__group')]",
        ]

        for selector in form_container_selectors:
            try:
                form_containers = await self.page.locator(selector).all()
                if form_containers:
                    logger.debug(
                        f"Found {len(form_containers)} form containers globally using selector: {selector}"
                    )
                    break
            except Exception:
                continue

        logger.debug(f"Found {len(form_containers)} form containers to inspect for errors")

        for section in form_containers:
            # Detect an error message within this section
            error_element: Any | None = None
            error_text: str = ""
            try:
                # Prefer the explicit message span inside the error container
                error_selectors = [
                    ".artdeco-inline-feedback--error .artdeco-inline-feedback__message",
                    ".artdeco-inline-feedback--error",
                    "[role='alert'][data-test-form-element-error-messages]",
                ]
                for selector in error_selectors:
                    loc = section.locator(selector)
                    cand_data = await loc.evaluate_all(
                        "els => els.map((e, i) => ({i, visible: e.offsetParent !== null, text: e.textContent?.trim() || ''}))"
                    )
                    match = next((d for d in cand_data if d["visible"] and d["text"]), None)
                    if match:
                        error_element = loc.nth(match["i"])
                        error_text = match["text"]
                        break
            except Exception:
                error_element = None

            if not error_element:
                continue

            # Find the textbox/textarea to correct within this section
            target_input: Any | None = None
            all_inputs_loc = section.locator(
                "input[type='text'], textarea, .artdeco-text-input--input"
            )
            vis_indices = await all_inputs_loc.evaluate_all(
                "els => els.map((e, i) => e.offsetParent !== null ? i : -1).filter(i => i >= 0)"
            )
            if vis_indices:
                target_input = all_inputs_loc.nth(vis_indices[0])

            if not target_input:
                # If no visible input found, skip this section
                logger.debug("Error found but no visible textbox in section; skipping")
                continue

            # Extract question/label text
            question_text = ""
            label: Any | None = None
            label_selectors = [
                "label",
                ".fb-dash-form-element__label",
                ".artdeco-text-input--label",
                "[data-test-single-typeahead-entity-form-title='true']",
            ]
            for selector in label_selectors:
                labels = await find_elements_safely(section, selector, "css selector")
                if labels:
                    label = labels[0]
                    break

            if label:
                try:
                    question_text = (await label.text_content() or "").lower().strip()
                    question_text = self._deduplicate_question_text(question_text)
                except Exception:
                    question_text = ""

            if not question_text:
                try:
                    alt = await target_input.get_attribute(
                        "aria-label"
                    ) or await target_input.get_attribute("placeholder")
                    if alt:
                        question_text = alt.strip()
                except Exception:
                    question_text = ""

            if not question_text:
                question_text = ""

            results.append((target_input, question_text, error_text))

        logger.debug(f"Textbox errors found: {len(results)}")
        return results

    async def _fill_textbox_question_errors(self) -> bool:
        """Find and try to fill with correct answers textbox question with errors (async)"""
        errors = await self._find_textbox_question_errors()
        if not errors:
            return False
        for error in errors:
            element, question_text, error_text = error
            logger.info(f"Answer textbox question with error: {question_text}. Error: {error_text}")
            if self._looks_like_writing_prompt(question_text):
                # A free-text/essay prompt that errors on submit is effectively
                # mandatory. Skip the job immediately instead of retrying a field
                # we never write.
                logger.info(
                    f"Mandatory free-text/essay question - skipping job: {question_text}"
                )
                raise NoInfoException(
                    f"Mandatory free-text/essay question requires writing: {question_text}"
                )
            if self._looks_like_salary_expectation_question(question_text):
                answer = self._salary_expectation_answer(is_numeric=True)
                logger.info(
                    "Using salary expectation while fixing textbox error for '%s': %s",
                    question_text,
                    answer,
                )
            else:
                answer = self.gpt_answerer.answer_question_textual_wide_range_with_error(
                    question_text,
                    error_text,
                    await element.get_attribute("value"),
                    self.previous_question_texts[:-1],
                )
            if answer.lower().startswith("no info"):
                raise NoInfoException(
                    f"Can't fix error: {error_text}. No info found for question: {question_text}"
                )
            answer = self.resume_anonymizer.deanonymize_text(answer)
            await element.fill(answer)
            await self._process_autocomplete_suggestions(element)
            self._save_questions(
                Question(question_type="text", question=question_text, answer=answer)
            )
        return True


if __name__ == "__main__":
    """Simple test for LinkedInEasyApplier functionality"""
    import asyncio
    from pathlib import Path

    import dotenv

    from config.app_config import TEST_MODE
    from config.constants import COVER_LETTER_DIR, OUTPUT_DIR_LINKEDIN, RESUME_DIR
    from src.job_manager.resume_anonymizer import ResumeAnonymizer
    from src.llm.llm_manager import GPTAnswerer
    from src.pydantic_models.job_models import Job
    from src.pydantic_models.prompt_models import ResumeStructure
    from src.resume_builder.resume_generator import ResumeGenerator
    from src.resume_builder.resume_manager import ResumeManager
    from src.resume_builder.style_manager import StyleManager

    # Wrapper removed in migration; use raw Playwright page
    from src.utils.browser_utils import create_playwright_browser, save_browser_session

    RESUME_STRUCTURED_FILE = Path(RESUME_DIR) / "structured_resume.yaml"
    RESUME_TEXT_FILE = Path(RESUME_DIR) / "resume_text.txt"
    paused = False

    async def check_pause():
        """Check if execution is paused and wait if needed"""
        global paused
        if paused:
            while paused:
                await asyncio.sleep(0.5)

    async def test_easy_applier():
        """Test LinkedInEasyApplier with a real LinkedIn job posting (async)"""
        logger.info("Starting LinkedInEasyApplier test...")

        # Test job URL
        job_url = "https://www.linkedin.com/jobs/view/4410066193"
        # Initialize Playwright browser
        try:
            browser, context, page = await create_playwright_browser()
            page = page
            logger.info("Playwright browser initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Playwright browser: {e}")
            return False

        # Create test job object
        test_job = Job(
            job_title="Junior Software Developer",
            company_name="Jobgether",
            location="Alaska, United States",
            url=job_url,
            job_description="This opportunity is ideal for recent graduates or early-career professionals eager to start their journey in software development. You will gain practical experience working on real-world applications while building proficiency in Java, Spring Boot, REST APIs, and full-stack workflows.",
            apply_method="Easy Apply",
        )

        try:
            # Load secrets for LLM
            secrets = dotenv.dotenv_values(".env")
            llm_api_key = secrets.get("llm_api_key", "")
            llm_proxy = secrets.get("llm_proxy", "")

            # Initialize GPT answerer
            gpt_answerer = GPTAnswerer(llm_api_key, llm_proxy)
            resume_structured = load_yaml_file(RESUME_STRUCTURED_FILE)
            resume_structured = ResumeStructure(**resume_structured).model_dump()
            with open(RESUME_TEXT_FILE, "r") as f:
                resume_text = f.read()

            # Set resume anonymizer and anonymize the resume information
            resume_anonymizer = ResumeAnonymizer(resume_structured)
            resume_anonymizer.anonymize_personal_information()
            resume_structured = resume_anonymizer.resume_anonymized
            resume_text = resume_anonymizer.anonymize_text(resume_text)

            gpt_answerer.set_resume(resume_structured, resume_text)
            gpt_answerer.set_job(test_job.model_dump(), is_test=True)

            # Initialize resume generator manager (mock for testing)
            style_manager = StyleManager()
            resume_generator = ResumeGenerator(gpt_answerer, resume_anonymizer)
            resume_generator_manager = ResumeManager(llm_api_key, style_manager, resume_generator)

            # Initialize LinkedInEasyApplier
            easy_applier = LinkedInEasyApplier(
                page,
                gpt_answerer,
                resume_anonymizer,
                resume_generator_manager,
                check_pause,
                Path(OUTPUT_DIR_LINKEDIN) / "answers.yaml",
                RESUME_DIR,
                COVER_LETTER_DIR,
                TEST_MODE,
            )
            if easy_applier.ready_made_resume_path is None:
                resume_generator_manager.choose_style()

            # Navigate to job page
            logger.info(f"Navigating to job page: {job_url}")
            await page.goto(job_url)
            await async_pause(3, 5)

            # Test the apply_to_job method
            logger.info("Testing LinkedInEasyApplier.apply_to_job method...")
            result = await easy_applier.apply_to_job(test_job)

            if result[0] == "Success":
                logger.info("✅ LinkedInEasyApplier test completed successfully!")
                return True
            else:
                logger.error("❌ LinkedInEasyApplier test failed - result is not Success")
                return False

        except Exception as e:
            logger.error(f"❌ LinkedInEasyApplier test failed with error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
        finally:
            # Keep browser open for manual inspection
            logger.info(
                "Test completed. Browser will remain open for 5 minutes for manual inspection..."
            )
            await async_pause(300, 300)
            try:
                await save_browser_session(context)
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass

    print("\nTesting full LinkedInEasyApplier functionality...")
    success = asyncio.run(test_easy_applier())
    if success:
        print("✅ LinkedInEasyApplier test passed!")
    else:
        print("❌ LinkedInEasyApplier test failed!")
