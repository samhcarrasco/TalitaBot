import base64
import os
import re
import traceback
from abc import ABC, abstractmethod
from typing import Any, List, Tuple

from httpx import HTTPStatusError

from config.logger_config import logger
from src.pydantic_models.job_models import Job, Question
from src.utils.browser_utils import debug_capture
from src.utils.utils import ConfigError, async_pause, load_yaml_file, sanitize_text, save_yaml_file


class NoInfoException(Exception):
    pass


class EasyApplyLimitReached(Exception):
    """Raised when LinkedIn's daily Easy Apply limit modal is detected.

    Signals the whole run should stop, not just skip the current job.
    """

    pass


class BaseEasyApplier(ABC):
    # Salary policy (Talita): no hard floor (she applies regardless of pay). If the
    # listing advertises a range, ask for its high end; otherwise ask her ideal
    # single number, or her ideal range when the field accepts a range rather than
    # a single value. Ideal band is 65-75k.
    SALARY_FLOOR = 0
    DEFAULT_SINGLE_SALARY = 70000
    DEFAULT_RANGE_LOW = 65000
    DEFAULT_RANGE_HIGH = 75000
    # Kept as a last-resort constant for any path that has no job context.
    DEFAULT_SALARY_EXPECTATION = str(DEFAULT_SINGLE_SALARY)
    SALARY_EXPECTATION_KEYWORDS = (
        "salary",
        "compensation",
        "base pay",
        "annual pay",
        "expected pay",
        "desired pay",
        "pay expectation",
        "pay expectations",
        "target pay",
        "earnings",
    )

    def __init__(self) -> None:
        super().__init__()
        self.ready_made_resume_path = None
        self.submitted_resume_path = None

    @abstractmethod
    async def apply_to_job(self, job: Job) -> None:
        pass

    @abstractmethod
    async def job_easy_apply(self, job: Job) -> Tuple[str, str]:
        pass

    @abstractmethod
    async def _handle_terms_of_service(self, section: Any) -> bool:
        pass

    @abstractmethod
    async def _find_and_handle_radio_question(self, section: Any) -> bool:
        pass

    @abstractmethod
    async def _find_and_handle_checkbox_question(self, section: Any) -> bool:
        pass

    @abstractmethod
    async def _find_and_handle_textbox_question(self, section: Any) -> bool:
        pass

    @abstractmethod
    async def _find_and_handle_dropdown_question(self, section: Any) -> bool:
        pass

    async def _find_and_handle_date_question(self, section: Any) -> bool:
        return False

    @classmethod
    def _looks_like_salary_expectation_question(cls, text: str) -> bool:
        normalized = sanitize_text(text or "")
        return any(
            re.search(r"\b" + re.escape(keyword) + r"\b", normalized)
            for keyword in cls.SALARY_EXPECTATION_KEYWORDS
        )

    # Two money amounts joined by a dash/"to", each optionally $-prefixed, comma-
    # grouped, and/or "k"-suffixed: e.g. "$90,000-$130,000", "90k to 130k".
    _SALARY_RANGE_RE = re.compile(
        r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
        r"\s*(?:-|–|—|to)\s*"
        r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?"
    )

    @classmethod
    def _parse_salary_high_end(cls, text: str) -> int | None:
        """Return the high end (whole dollars) of a salary range found in text.

        Returns None when no usable annual range is present. Hourly-looking
        ranges (high end under $1,000 with no 'k') are ignored so we do not
        anchor to an hourly rate.
        """
        if not text:
            return None

        def to_dollars(num: str, suffix: str | None) -> float:
            value = float(num.replace(",", ""))
            if suffix and suffix.lower() == "k":
                value *= 1000
            return value

        for match in cls._SALARY_RANGE_RE.finditer(text):
            low = to_dollars(match.group(1), match.group(2))
            high = to_dollars(match.group(3), match.group(4))
            high = max(low, high)
            if high >= 1000:  # ignore hourly rates / non-salary numbers
                return int(high)
        return None

    def _salary_expectation_answer(self, is_numeric: bool) -> str:
        """Answer a salary-expectation field per Talita's policy.

        - Listing advertises a range -> its high end (SALARY_FLOOR is 0, so no floor).
        - No range, single-value field -> DEFAULT_SINGLE_SALARY.
        - No range, range-accepting field -> "DEFAULT_RANGE_LOW - DEFAULT_RANGE_HIGH".
        """
        job = getattr(self, "current_job", None)
        listing_text = ""
        if job is not None:
            listing_text = " ".join(
                part
                for part in (
                    getattr(job, "salary_range", None) or "",
                    getattr(job, "job_description", None) or "",
                )
                if part
            )
        high_end = self._parse_salary_high_end(listing_text)
        if high_end is not None:
            return str(max(high_end, self.SALARY_FLOOR))
        if is_numeric:
            return str(self.DEFAULT_SINGLE_SALARY)
        return f"{self.DEFAULT_RANGE_LOW:,} - {self.DEFAULT_RANGE_HIGH:,}"

    async def _create_and_upload_resume(self, element: Any, job: Job) -> None:
        try:
            os.makedirs(self.generated_resume_dir, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create directory: {self.generated_resume_dir}. Error: {e}")
            raise

        if self.ready_made_resume_path is not None:
            file_path_pdf = os.path.abspath(str(self.ready_made_resume_path))
            logger.info(f"Using ready-made resume: {file_path_pdf}")
        else:
            file_path_pdf = os.path.join(
                self.generated_resume_dir, f"CV_{job.company_name}_{job.job_title}.pdf"
            )
            if os.path.exists(file_path_pdf):
                logger.info(f"Resume already exists, reusing cached file: {file_path_pdf}")
            else:
                while True:
                    try:
                        resume_pdf_base64 = await self.resume_generator_manager.pdf_base64()
                        with open(file_path_pdf, "wb") as f:
                            f.write(base64.b64decode(resume_pdf_base64))
                        logger.info(f"Resume successfully generated and saved to: {file_path_pdf}")
                        break
                    except HTTPStatusError as e:
                        if e.response.status_code == 429:
                            retry_after = e.response.headers.get("retry-after")
                            retry_after_ms = e.response.headers.get("retry-after-ms")
                            if retry_after:
                                wait_time = int(retry_after)
                            elif retry_after_ms:
                                wait_time = int(retry_after_ms) / 1000.0
                            else:
                                wait_time = 20
                            logger.warning(
                                f"Rate limit exceeded, waiting {wait_time}s before retrying..."
                            )
                            await async_pause(wait_time, wait_time + 1)
                        else:
                            logger.error(f"HTTP error: {e}")
                            raise
                    except Exception as e:
                        logger.error(f"Failed to generate resume: {e}")
                        if "RateLimitError" in str(e):
                            logger.warning("Rate limit error encountered, retrying...")
                            await async_pause(20, 40)
                        else:
                            raise

        file_size = os.path.getsize(file_path_pdf)
        max_file_size = 2 * 1024 * 1024  # 2 MB
        if file_size > max_file_size:
            logger.error(f"Resume file size exceeds 2 MB: {file_size} bytes")
            raise ValueError("Resume file size exceeds the maximum limit of 2 MB.")

        file_extension = os.path.splitext(file_path_pdf)[1].lower()
        if file_extension not in {".pdf", ".doc", ".docx"}:
            logger.error(f"Invalid resume file format: {file_extension}")
            raise ValueError(
                "Resume file format is not allowed. Only PDF, DOC, and DOCX formats are supported."
            )

        try:
            abs_path = os.path.abspath(file_path_pdf)
            await element.set_input_files(abs_path)
            self.submitted_resume_path = abs_path
            await async_pause(1, 2)
            logger.debug(f"Resume created and uploaded successfully: {file_path_pdf}")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Resume upload failed: {tb_str}")
            await debug_capture(self.page, "resume_upload_error")
            raise Exception(f"Upload failed: \nTraceback:\n{tb_str}")

    async def _process_form_section(self, section: Any) -> None:
        """Process form section by dispatching to appropriate handler (async)"""
        logger.debug("Processing form section")
        if await self._handle_terms_of_service(section):
            logger.debug("Handled terms of service")
            return
        if await self._find_and_handle_radio_question(section):
            logger.debug("Handled radio question")
            return
        if await self._find_and_handle_checkbox_question(section):
            logger.debug("Handled checkbox question")
            return
        if await self._find_and_handle_dropdown_question(section):
            logger.debug("Handled dropdown question")
            return
        if await self._find_and_handle_date_question(section):
            logger.debug("Handled date question")
            return
        if await self._find_and_handle_textbox_question(section):
            logger.debug("Handled textbox question")
            return
        logger.debug("Form section not handled")

    def _save_questions(self, question_data: Question) -> None:
        """Save questions to YAML file"""
        question_data.question = sanitize_text(question_data.question)

        logger.debug(f"Checking if question data already exists: {question_data}")
        try:
            should_be_saved: bool = not self._answer_contains_company_name(question_data.answer)
            self.all_questions = [
                q for q in self.all_questions if q.question != question_data.question
            ]
            if should_be_saved:
                logger.debug("New question found, appending to YAML")
                self.all_questions.append(question_data)
                save_yaml_file(
                    self.answers_file, [question.model_dump() for question in self.all_questions]
                )
            else:
                logger.debug("Question already exists, skipping save")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error saving questions data to YAML file: {tb_str}")
            raise Exception(f"Error saving questions data to YAML file: \nTraceback:\n{tb_str}")

    def _answer_contains_company_name(self, answer: str) -> bool:
        """Check if answer contains company name"""
        return (
            isinstance(answer, str)
            and self.current_job.company_name is not None
            and self.current_job.company_name in answer
        )

    def _find_cached_question(
        self, question_text: str, question_type: str | None = None
    ) -> Question | None:
        """Find a cached answer by exact question text, preferring the same field type."""
        current_question_sanitized = sanitize_text(question_text)
        same_type_match = None
        any_type_match = None

        for item in self.all_questions:
            if item.question != current_question_sanitized:
                continue

            if item.question_type == question_type:
                same_type_match = item
                break

            if any_type_match is None:
                any_type_match = item

        return same_type_match or any_type_match

    def _load_questions(self) -> List[Question]:
        logger.info(f"Loading questions from YAML file: {self.answers_file}")
        try:
            answers_file = self.answers_file
            if not answers_file.exists():
                legacy_answers_file = answers_file.parent.parent / answers_file.name
                if legacy_answers_file.exists():
                    logger.info(
                        "Using legacy shared answers file because platform-specific file is missing: "
                        f"{legacy_answers_file}"
                    )
                    self.answers_file = legacy_answers_file
                    answers_file = legacy_answers_file

            data = load_yaml_file(answers_file)
            logger.info("Questions loaded successfully from YAML")
            if not data:
                return []
            return [Question(**question) for question in data]
        except ConfigError:
            logger.warning("Answers file not found, returning empty list")
            return []
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error loading questions data from YAML file: {tb_str}")
            raise Exception(f"Error loading questions data from YAML file: \nTraceback:\n{tb_str}")
