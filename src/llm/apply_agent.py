import asyncio
import json
import os
from datetime import datetime
from pathlib import Path

from browser_use import Agent, Browser, ChatAnthropic, ChatGoogle, ChatOllama, ChatOpenAI, Tools
from browser_use.tools.views import UploadFileAction

from config.app_config import (
    APPLY_AGENT_MODEL,
    APPLY_AGENT_MODEL_TYPE,
    HEADLESS_MODE,
    LLM_MODEL_TYPE,
)
from config.constants import CUSTOM_COST_PER_TOKEN, LOG_DIR, RESUME_DIR, cost_per_token
from config.logger_config import logger
from src.dashboard.runtime import emit_event
from src.pydantic_models.log_models import LLMCall
from src.utils.utils import append_yaml_file, get_ready_made_resume

# DOM guard injected during dry runs. Blocks clicks on FINAL submit-style buttons
# while allowing navigation buttons (Next/Continue/Review), so the agent can reach
# the final review step but cannot actually submit. Installed as a capturing
# listener so it intercepts synthetic CDP click events too.
_DRY_RUN_SUBMIT_BLOCK_JS = r"""
(() => {
  if (window.__dryRunGuardInstalled) return;
  window.__dryRunGuardInstalled = true;
  window.__dryRunSubmitBlocked = 0;
  const SUBMIT_RE = /(submit|apply now|send application|send my application|finish( and submit)?|complete application|confirm( and submit)?)/i;
  const NAV_RE = /^(next|continue|review|back|previous|save( and continue)?|save)$/i;
  const looksLikeSubmit = (el) => {
    try {
      const t = (el.innerText || el.textContent || el.value ||
                 (el.getAttribute && el.getAttribute('aria-label')) || '').trim();
      if (!t) return false;
      if (NAV_RE.test(t) && !SUBMIT_RE.test(t)) return false; // allow navigation buttons
      return SUBMIT_RE.test(t);
    } catch (e) { return false; }
  };
  const submitAncestor = (node) => {
    let el = node;
    for (let i = 0; i < 6 && el; i++) {
      if (el.tagName && /^(button|a|input)$/i.test(el.tagName) && looksLikeSubmit(el)) return el;
      el = el.parentElement;
    }
    return null;
  };
  document.addEventListener('click', (e) => {
    const t = submitAncestor(e.target);
    if (t) {
      e.preventDefault();
      e.stopImmediatePropagation();
      window.__dryRunSubmitBlocked++;
      console.warn('[DRY-RUN] blocked submit click:', (t.innerText || t.value || '').trim());
    }
  }, true);
})();
"""


class ApplyAgent:
    def __init__(
        self,
        api_key: str = None,
        browser_storage_state: str = None,
        llm_api_url: str = None,
        user_email: str = None,
    ) -> None:
        self.api_key = api_key
        self.user_email = user_email
        self.model = APPLY_AGENT_MODEL
        # The off-site agent needs structured output, which DeepSeek lacks, so it
        # runs on its own provider (APPLY_AGENT_MODEL_TYPE) independent of the
        # scoring/answering provider (LLM_MODEL_TYPE). Falls back to the global type.
        self.model_type = APPLY_AGENT_MODEL_TYPE or LLM_MODEL_TYPE
        self.llm_api_url = llm_api_url
        self.llm = self.select_model_type(self.model_type, self.llm_api_url)
        self.calls_log = os.path.join(Path(LOG_DIR), "llm_api_calls.yaml")
        self.agent = None
        self.resume_readable = None
        self.browser_storage_state = str(Path(browser_storage_state).absolute())
        storage_state = (
            self.browser_storage_state if Path(self.browser_storage_state).exists() else None
        )
        if storage_state is None:
            logger.warning(
                f"Browser storage state file not found at {self.browser_storage_state}. "
                "Continuing without persisted cookies/localStorage."
            )
        self.storage_state = storage_state

    def _sanitized_storage_state(self) -> str | None:
        """Return a storage-state path that browser-use can load without crashing.

        The saved LinkedIn storage state can contain partitioned cookies (a
        ``partitionKey`` field) that browser-use's CDP cookie injection cannot
        deserialize ("CBOR: map start expected ..."). That makes the
        StorageStateWatchdog fail and destabilises the agent's browser mid-form
        (empty DOM / detached targets / "browser not connected"). Strip
        ``partitionKey`` from every cookie and hand the agent a sanitized copy
        instead. Read fresh each time so newly saved cookies are picked up; fall
        back to the original path on any error.
        """
        if not self.storage_state:
            return None
        try:
            with open(self.storage_state, "r", encoding="utf-8") as f:
                state = json.load(f)
            cookies = state.get("cookies")
            stripped = False
            if isinstance(cookies, list):
                for cookie in cookies:
                    if isinstance(cookie, dict) and cookie.pop("partitionKey", None) is not None:
                        stripped = True
            if not stripped:
                return self.storage_state
            sanitized_path = str(
                Path(self.browser_storage_state).with_name("browser_state.agent.json")
            )
            with open(sanitized_path, "w", encoding="utf-8") as f:
                json.dump(state, f)
            logger.debug("Wrote sanitized agent storage state (partitioned cookies stripped)")
            return sanitized_path
        except Exception as e:
            logger.warning(f"Could not sanitize storage state, using original: {e}")
            return self.storage_state

    def _create_browser(self) -> Browser:
        # Pass storage_state WITHOUT a persistent user_data_dir. Otherwise browser-use
        # defaults to a temp user_data_dir and then force-overwrites it from
        # storage_state via CDP, tripping the StorageStateWatchdog and destabilising
        # the session. storage_state alone carries the cookies we need.
        return Browser(
            headless=HEADLESS_MODE,
            storage_state=self._sanitized_storage_state(),
            user_data_dir=None,
        )

    def select_model_type(self, model_type: str, llm_api_url: str) -> None:
        """Select the model to use."""
        self.model_type = model_type
        if model_type == "gemini":
            if not self.api_key:
                raise ValueError("API key is required for Gemini model")
            llm = ChatGoogle(api_key=self.api_key, model=self.model)
        elif model_type == "openai":
            if not self.api_key:
                raise ValueError("API key is required for OpenAI model")
            llm = ChatOpenAI(api_key=self.api_key, model=self.model, reasoning_effort="minimal")
        elif model_type == "claude":
            if not self.api_key:
                raise ValueError("API key is required for Claude model")
            llm = ChatAnthropic(api_key=self.api_key, model=self.model)
        elif model_type == "ollama":
            if llm_api_url:
                import os

                os.environ["OLLAMA_BASE_URL"] = llm_api_url
            llm = ChatOllama(model=self.model, base_url=llm_api_url)
        elif model_type == "openrouter":
            llm = ChatOpenAI(
                api_key=self.api_key,
                model=self.model,
                base_url="https://openrouter.ai/api/v1",
            )
        elif model_type == "deepseek":
            if not self.api_key:
                raise ValueError("API key is required for DeepSeek model")
            llm = ChatOpenAI(
                api_key=self.api_key,
                model=self.model,
                base_url="https://api.deepseek.com/v1",
            )
        else:
            raise ValueError(f"Unsupported model type: {model_type}")
        return llm

    def set_resume(self, resume_readable: str) -> None:
        """Add resume for analysis."""
        self.resume_readable = resume_readable

    async def apply(self, job_url: str, dry_run: bool = False) -> None:
        """Apply to the job using AI Agent.

        When dry_run is True, the agent fills the form and navigates to the final
        review/submit step but must NOT submit. Safety is layered: a no-submit task
        prompt, a no-op `finish_without_submitting` tool the agent calls instead of
        submitting, and a best-effort DOM guard that blocks clicks on final submit
        buttons (injected via CDP per browser target).
        """
        resume_pdf_path = str(get_ready_made_resume())
        emit_event(
            "agent_apply_started",
            "External apply agent started" + (" (DRY RUN - no submit)" if dry_run else ""),
            url=job_url,
        )

        tools = Tools()

        @tools.action(
            description="Get an UploadFileAction for my resume PDF file (use with upload_file_to_element if needed)"
        )
        async def upload_resume(browser_session, index: int = 0):  # noqa: ARG001
            return UploadFileAction(path=resume_pdf_path, index=index)

        browser_storage_state = self.browser_storage_state

        @tools.action(
            description=(
                "Call this when the website requires email verification or confirmation "
                "before you can proceed. This pauses the task and asks the user to verify "
                "their email, then saves the browser session so the user won't need to "
                "log in again."
            )
        )
        async def wait_for_email_verification(browser_session):
            print("\n" + "=" * 60)
            print("EMAIL VERIFICATION REQUIRED")
            print("Please check your email and confirm your account.")
            print("Once done, press Enter to continue...")
            print("=" * 60 + "\n")
            await asyncio.get_event_loop().run_in_executor(None, input)
            await browser_session.export_storage_state(output_path=browser_storage_state)
            logger.info(f"Browser session saved to {browser_storage_state}")
            return "User confirmed email verification. Browser session saved. Proceeding with the application."

        @tools.action(
            description=(
                "DRY RUN ONLY: call this when you have reached the final review/submit "
                "step of the application, INSTEAD of clicking the submit/apply button. "
                "Records that the final page was reached without submitting, then finish the task."
            )
        )
        async def finish_without_submitting(browser_session, summary: str = ""):  # noqa: ARG001
            logger.info(f"[dry-run] Agent reached final step WITHOUT submitting. {summary}")
            return (
                "Acknowledged: final review step reached without submitting. "
                "Do not click submit. You may finish the task now."
            )

        # Tracks whether the agent verified a real submission. Defaults to NOT
        # submitted, so the bot only counts an application as sent when the agent
        # explicitly confirms a confirmation/success page (no more false "Success").
        submission_state = {"submitted": False, "detail": ""}

        @tools.action(
            description=(
                "Call this ONCE at the very end of a REAL (non-dry-run) application to "
                "report the outcome. Set submitted=True ONLY if you clicked the final "
                "submit button AND saw a confirmation/thank-you page or an explicit "
                "success message. If you could not submit (blocked, error, registration "
                "wall, missing required info, etc.), set submitted=False and explain why "
                "in detail. Then finish the task."
            )
        )
        async def report_submission_result(  # noqa: ARG001
            browser_session, submitted: bool, detail: str = ""
        ):
            submission_state["submitted"] = bool(submitted)
            submission_state["detail"] = detail
            logger.info(
                f"[apply-agent] Submission outcome reported: submitted={submitted}. {detail}"
            )
            return "Submission outcome recorded. You may finish the task now."

        if dry_run:
            closing = """
        *** IMPORTANT — THIS IS A DRY RUN. DO NOT SUBMIT THE APPLICATION. ***
            - Fill the entire application accurately and navigate through EVERY step until you reach the FINAL review/submit step (the screen whose only remaining action is a final "Submit" / "Apply" / "Send application" / "Finish" button).
            - You must NOT click any final submit/apply/send/finish button. You must NOT submit the application under any circumstances. Clicking "Next" / "Continue" / "Review" to move forward IS allowed and expected.
            - When you reach the final step and the only remaining action would be to submit, STOP and call the `finish_without_submitting` tool with your summary. Do NOT click the submit button.
            - You are done ONLY when you have either (a) reached the final submit step and called `finish_without_submitting`, or (b) confirmed you genuinely cannot proceed.
            - At the end, structure your final_result as:
                1) a human-readable summary of all detections and actions performed
                2) a list of all questions encountered on the page (including any screening questions)
                3) explicit confirmation that you did NOT submit, and the exact text of the final submit button you stopped at
        """
        else:
            closing = """
        *** IMPORTANT — SUBMIT, THEN VERIFY ***
            - Complete every step, then locate and click the final submit/apply button to actually SUBMIT the application.
            - To find it, read the interactive-elements list and click the button by its role/visible text ("Submit application", "Submit", "Apply", "Send application", "Finish"). The submit button is almost always at the very bottom of the form: scroll to the END of the page ONCE, then click it from the elements list — do not keep scrolling hoping it appears.
            - Do NOT repeat the same scroll or search action over and over. If you have reached the bottom and still don't see a submit button, re-read the interactive elements and click the correct one instead of scrolling again.
            - You must click the submit button YOURSELF. A success message that appears without you having clicked submit is not your submission — keep going and click submit.
            - After clicking submit, VERIFY it went through: look for a confirmation/thank-you page, a "your application has been submitted" message, or a success banner. Do not assume success — confirm it visually.
            - Then call the `report_submission_result` tool exactly once:
                * submitted=True ONLY if YOU clicked the submit button and then saw a real confirmation/success page; put the confirmation text in `detail`.
                * submitted=False if you could NOT submit (blocked, error, registration wall, missing required info, etc.); explain why in `detail`.
            - You are NOT done until you have called `report_submission_result`. Calling it is mandatory whether you succeeded or failed.
            - At the end, structure your final_result as:
                1) a human-readable summary of all detections and actions performed
                2) a list of all questions encountered on the page (including any screening questions)
                3) whether the application was actually submitted and confirmed, with the confirmation text you saw
        """

        task = f"""
        - Your goal is to apply to the job at: {job_url}
        - Use the information from my resume (source of truth) and any additional information already present on the page.
        - If you cannot apply, finish the task (do not try different URLs).

        - Follow these instructions carefully:
            - If anything pops up that blocks the form, close it and continue.
            - Do not skip required fields. If an optional field is present, fill it if possible using my resume/context.
            - Fill the form from top to bottom; do not skip a field to come back later.
            - Some text boxes may have dropdown suggestions: after filling a textbox, check for a dropdown and select the correct option.

        - Resume:
            - You may upload my resume PDF when the application asks for it.
            - The resume file is available as: {resume_pdf_path}
            - Prefer using the built-in upload_file_to_element action; if the page flow needs it, you can use the upload_resume tool to produce an UploadFileAction.

        - If you are asked to register an account, use my email: {self.user_email} and password: {self.user_email.split("@")[0] + "123456" if self.user_email else "unknown"}

        - If an email verification or confirmation step appears, call the wait_for_email_verification tool immediately — do NOT give up or mark the task as failed. After the tool returns, continue the application.

        - Before you start, create a step-by-step plan to complete the entire application. Delegate a step for each field/section you encounter.
{closing}
        ## My resume text (source of truth):
        {self.resume_readable}
        """

        available_file_paths = [resume_pdf_path]

        # Best-effort DOM guard for dry runs: block clicks on final submit buttons
        # (allows Next/Continue/Review so the agent can still reach the final page).
        # This is a backstop only — the no-submit prompt and finish_without_submitting
        # tool are the primary guarantees, since CDP details vary across browser_use.
        injected_targets: set = set()

        async def _install_submit_block(agent) -> None:
            try:
                session = agent.browser_session
                cdp_session = await session.get_or_create_cdp_session(target_id=None, focus=True)
                client, sid = cdp_session.cdp_client, cdp_session.session_id
                # Apply to the current document now...
                await client.send.Runtime.evaluate(
                    params={"expression": _DRY_RUN_SUBMIT_BLOCK_JS, "returnByValue": True},
                    session_id=sid,
                )
                # ...and persist across future document loads in this target.
                if cdp_session.target_id not in injected_targets:
                    await client.send.Page.addScriptToEvaluateOnNewDocument(
                        params={"source": _DRY_RUN_SUBMIT_BLOCK_JS}, session_id=sid
                    )
                    injected_targets.add(cdp_session.target_id)
            except Exception as e:
                logger.debug(
                    f"[dry-run] submit-block injection failed (relying on prompt + tool guard): {e}"
                )

        browser = self._create_browser()
        try:
            self.agent = Agent(
                task=task,
                browser=browser,
                llm=self.llm,
                tools=tools,
                # Vision lets the agent SEE the form and catch fields that are
                # missing or ambiguous in the text DOM (e.g. skipped name fields).
                # Gemini supports it; browser-use auto-disables vision for providers
                # that don't (e.g. DeepSeek), so this is safe across model types.
                use_vision=True,
                use_thinking=False,
                save_conversation_path=Path(LOG_DIR).absolute() / "apply_agent_conversation",
                available_file_paths=available_file_paths,
            )
            await self.agent.run(on_step_start=_install_submit_block if dry_run else None)
        finally:
            await browser.stop()

        self._log_token_usage(task)
        emit_event("agent_apply_completed", "External apply agent completed", url=job_url)
        return submission_state

    def _log_token_usage(self, task: str) -> None:
        """Log AI Agent token usage and calculate the total cost"""
        token_usage = self.agent.token_cost_service.get_usage_tokens_for_model(self.model)
        input_tokens, output_tokens = token_usage.prompt_tokens, token_usage.completion_tokens
        total_tokens = input_tokens + output_tokens
        logger.info(
            f"Token usage - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}"
        )
        prompt_cost, completion_cost = cost_per_token(
            model=self.model.replace("google/", ""),
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            custom_cost_per_token=CUSTOM_COST_PER_TOKEN,
        )
        total_cost = prompt_cost + completion_cost
        logger.info(f"Total cost calculated: {total_cost}")

        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            log_entry = LLMCall(
                model_name=self.model,
                timestamp=current_time,
                prompts={
                    "prompt_1": task,
                    "prompt_2": "<Some browser content>",
                },
                parsed_reply="<Some reply from agent>",
                total_tokens=total_tokens,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                response_time_seconds=0.0,
                total_cost=total_cost,
            )
            logger.debug(f"Log entry created: {log_entry}")
        except KeyError as e:
            logger.error(f"Error creating log entry: missing key {str(e)} in parsed_reply")
            raise

        append_yaml_file(Path(self.calls_log), log_entry.model_dump())

        return total_cost

    async def apply_to_job(self, job_url: str, dry_run: bool = False) -> tuple[str, str]:
        """Apply to job, handling event loop properly.

        When dry_run is True, the agent fills the form to the final review step but
        does not submit; the result is reported as a non-counting "Skip".
        """
        # Directly await the apply method since we're already in an async context
        try:
            state = await self.apply(job_url, dry_run=dry_run)
            if dry_run:
                return ("Skip", "Dry run - reached final review step, not submitted")
            # Only count as a real success when the agent verified submission via
            # report_submission_result. Otherwise treat it as not submitted so the
            # "Applications sent" counter reflects confirmed submissions only.
            state = state or {"submitted": False, "detail": ""}
            if state.get("submitted"):
                return ("Success", state.get("detail") or "Submission confirmed")
            return ("Skip", f"Could not submit: {state.get('detail') or 'no submission confirmed by agent'}")
        except Exception as e:
            logger.error(f"Error applying to job: {e}")
            emit_event(
                "agent_apply_failed", "External apply agent failed", url=job_url, error=str(e)
            )
            return ("Error", str(e))


if __name__ == "__main__":
    """Test ApplyAgent functionality"""
    import traceback

    import dotenv

    from config.constants import BROWSER_STORAGE_STATE
    from src.pydantic_models.prompt_models import ResumeStructure
    from src.utils.utils import load_yaml_file

    async def test_apply_agent():
        """Test ApplyAgent with a real LinkedIn job posting"""
        logger.info("Starting ApplyAgent test...")

        try:
            # Load secrets for LLM
            secrets = dotenv.dotenv_values(".env")
            llm_api_key = secrets.get("llm_api_key", "")

            if not llm_api_key:
                logger.error("❌ LLM API key not found in .env file")
                return False

            # Initialize ApplyAgent
            apply_agent = ApplyAgent(
                llm_api_key, BROWSER_STORAGE_STATE, user_email=secrets.get("linkedin_email", "")
            )
            logger.info("ApplyAgent initialized successfully")

            # Load resume data
            RESUME_STRUCTURED_FILE = Path(RESUME_DIR) / "structured_resume.yaml"
            RESUME_TEXT_FILE = Path(RESUME_DIR) / "resume_text.txt"

            if not RESUME_STRUCTURED_FILE.exists():
                logger.error(f"❌ Resume structured file not found: {RESUME_STRUCTURED_FILE}")
                return False

            if not RESUME_TEXT_FILE.exists():
                logger.error(f"❌ Resume text file not found: {RESUME_TEXT_FILE}")
                return False

            # Load and set resume data
            resume_structured = load_yaml_file(RESUME_STRUCTURED_FILE)
            resume_structured = ResumeStructure(**resume_structured).model_dump()

            with open(RESUME_TEXT_FILE, "r") as f:
                resume_text = f.read()

            # Set resume and job for the agent
            apply_agent.set_resume(resume_text)
            logger.info("Resume and job data set successfully")

            # Test the apply_to_job method
            vacancy_url = "https://app.searchwithjack.com/jobs/4372944?utm_source=linkedin-direct-apply-4372944&comet_source=linkedin"
            logger.info(f"Testing ApplyAgent.apply_to_job method with job: {vacancy_url}")
            logger.info("This will open a browser and attempt to apply to the job...")

            # Run the application
            await apply_agent.apply_to_job(vacancy_url)

            logger.info("✅ ApplyAgent test completed successfully!")
            logger.info("Check the browser window to see the application process")
            return True

        except Exception as e:
            logger.error(f"❌ ApplyAgent test failed with error: {e}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False

    # Run the test
    success = asyncio.run(test_apply_agent())
    if success:
        print("✅ Test passed!")
    else:
        print("❌ Test failed!")
