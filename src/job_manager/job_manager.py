import os
import traceback
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from config.app_config import COLLECT_INFO_MODE, JOB_SITE, MAX_APPLIES_NUM, TEST_MODE
from config.constants import OUTPUT_DIR_INDEED, OUTPUT_DIR_LINKEDIN
from config.logger_config import logger
from src.dashboard.runtime import emit_event
from src.pydantic_models.job_models import Job, JobInfo, JobManagerCache
from src.telegram.telegram_manager import TelegramReportSender
from src.utils.application_tracker import append_application
from src.utils.run_summary import build_run_summary, show_summary_popup
from src.utils.utils import sanitize_text, save_yaml_file

# config/app_config.py is user-owned and may predate this flag, so default it on.
try:
    from config.app_config import SHOW_RUN_SUMMARY_POPUP
except ImportError:
    SHOW_RUN_SUMMARY_POPUP = True

# Likewise default the re-apply blacklist window (days) if the user's config
# predates it. 0 disables the time limit (an applied position blocks forever).
try:
    from config.app_config import REAPPLY_BLACKLIST_DAYS
except ImportError:
    REAPPLY_BLACKLIST_DAYS = 14

OUTPUT_DIR = OUTPUT_DIR_LINKEDIN if JOB_SITE == "linkedin" else OUTPUT_DIR_INDEED
LAST_RUN_FILE = Path(OUTPUT_DIR) / "last_run.yaml"


class BaseJobManager(ABC):
    @abstractmethod
    def start_applying(self) -> None:
        pass

    @abstractmethod
    def apply_job(self, vacancy: Any) -> str:
        pass

    @abstractmethod
    def easy_apply(self, job: Job) -> Tuple[str, str]:
        pass

    @staticmethod
    def _define_output_file(filename: str) -> Path:
        """Define the path to the output file"""
        try:
            output_file = os.path.join(Path(OUTPUT_DIR), filename)
            logger.info(f"The path to the output file has been defined: {output_file}")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in defining the location of the file: {tb_str}")
            raise
        return output_file

    def set_parameters(self, parameters: Dict[str, Any]):
        """Setting job manager parameters"""
        logger.info("Setting job manager parameters")
        self.max_applies_num = MAX_APPLIES_NUM
        self.apply_once_at_company = parameters.get("apply_once_at_company", True)
        company_blacklist = parameters.get("company_blacklist") or []
        self.job_blacklist = [sanitize_text(j) for j in company_blacklist]
        self.success_companies = self._load_companies_from_yaml("success.yaml")
        self.skipped_companies = self._load_companies_from_yaml("skipped.yaml")
        self.failed_companies = self._load_companies_from_yaml("failed.yaml")
        self.seen_answers = self._load_data_from_yaml("answers.yaml")
        self.skill_stat = self._load_data_from_yaml("skill_stat.yaml")
        self.interesting_jobs = self._load_data_from_yaml("interesting_jobs.yaml")
        self.interesting_jobs = [JobInfo(**job) for job in self.interesting_jobs]
        self.cache = self._load_cache()
        self.applies_num = 0
        self.previous_apply_number = self._check_the_previous_apply_number()
        self.success_applies_num = self.previous_apply_number
        self.total_applies_num = self.cache.total_applies_num
        # End-of-run summary tracking: count results by type and record the start
        # time so _finalize_run_summary can report what happened this run.
        self.results_counter: Dict[str, int] = {}
        self.run_started_at = datetime.now()
        logger.info("Parameters successfully set")

    def set_answerer_and_agent(self, llm_answerer_component: Any, llm_agent_component: Any):
        """Set LLM for answering questions and writing cover letters"""
        self.llm_answerer_component = llm_answerer_component
        self.llm_agent_component = llm_agent_component

    def set_resume(self, resume: Dict[str, Any]) -> None:
        """Add resume for analysis"""
        self.resume = resume

    def set_resume_generator_manager(self, resume_generator_manager: Any):
        """Set resume generator manager for writing resumes"""
        self.resume_generator_manager = resume_generator_manager

    def set_pause_checker(self, pause_checker):
        """Set pause checker function for pausing execution"""
        self.pause_checker = pause_checker

    def _extract_skills_from_vacancy(self, job: Job) -> List[str]:
        """Extract skills from vacancy"""
        logger.info(f"Extracting skills from vacancy: {job.job_title}")
        skills = self.llm_answerer_component.extract_skills_from_vacancy(job.job_description)
        self.job_key_skills = skills
        return str(skills).replace("[", "").replace("]", "").replace("'", "").replace('"', "")

    def _update_skill_stat(self, skills) -> None:
        """Update the statistics of the most demanded skills in the vacancy and save it to a file"""
        logger.info("Updating the statistics of the most demanded skills in the vacancy")
        for skill in skills:
            if ";" in skill:
                processed_skills = self._process_skill_string(skill)
                for s in processed_skills:
                    self.skill_stat[s] = self.skill_stat.get(s, 0) + 1
            else:
                self.skill_stat[skill] = self.skill_stat.get(skill, 0) + 1
        self.skill_stat = sorted(self.skill_stat.items(), key=lambda x: x[1], reverse=True)
        self.skill_stat = {k: v for k, v in self.skill_stat}
        self._save_data_to_yaml(self.skill_stat, "skill_stat.yaml", sort_keys=False)

    def _process_skill_string(self, skill_string: str) -> List[str]:
        """Split the string with skills into a list of skills"""
        processed_skills = []
        for part in skill_string.split(";"):
            cleaned = "".join(char for char in part if char.isalnum() or char.isspace())
            cleaned = cleaned.strip()
            if cleaned:
                processed_skills.append(cleaned)
        return processed_skills

    def _save_company(
        self,
        job: Job,
        apply_result: Tuple[str, str],
        vacancy: Dict[str, Any],
        evaluation: Dict[str, Any] | None = None,
    ) -> None:
        """Determine in which category to save the company and save it to the corresponding YAML file"""
        company_name = job.company_name
        company_job_title = job.job_title
        result, reason = apply_result
        evaluation = evaluation or {}

        if result == "Success":
            companies = self.success_companies
            filename = "success.yaml"
        elif result == "Skip":
            companies = self.skipped_companies
            filename = "skipped.yaml"
        else:
            companies = self.failed_companies
            filename = "failed.yaml"

        seen_companies = companies

        try:
            job_info = JobInfo(
                job_title=company_job_title,
                company_name=company_name,
                url=vacancy["url"],
                skip_reason=reason,
                skills=evaluation.get("skills"),
                interest_score=evaluation.get("interest_score"),
                interest_reason=evaluation.get("interest_reason"),
                llm_time_seconds=(
                    self.llm_answerer_component.get_job_llm_time_seconds(vacancy["url"])
                    if self.llm_answerer_component
                    else 0.0
                ),
                executed_at=datetime.now().isoformat(timespec="seconds"),
                submitted_resume_path=evaluation.get("submitted_resume_path"),
            )
        except Exception as e:
            logger.warning(f"Error in saving job info: {e}")
            return

        if company_name:
            if company_name in seen_companies:
                existing_jobs = seen_companies[company_name]
                if any(
                    saved_job.get("url") == vacancy["url"]
                    or saved_job.get("job_title") == company_job_title
                    for saved_job in existing_jobs
                ):
                    logger.info("Vacancy already saved in output file, skipping duplicate entry")
                    return
                existing_jobs.append(job_info.model_dump())
            else:
                seen_companies[company_name] = [job_info.model_dump()]

        self._save_company_to_yaml(filename, companies)

        if result == "Success":
            try:
                append_application({
                    "applied_at": job_info.executed_at or datetime.now().isoformat(timespec="seconds"),
                    "company_name": job.company_name,
                    "job_title": job.job_title,
                    "location": job.location,
                    "is_remote": job.is_remote,
                    "salary_range": job.salary_range or "",
                    "employment_type": job.employment_type or "",
                    "experience_level": job.experience_level or "",
                    "interest_score": job_info.interest_score or "",
                    "submitted_resume_path": job_info.submitted_resume_path or "",
                    "job_id": job.job_id or "",
                    "url": job.get_str_url(),
                })
                logger.info(f"Application logged to CSV: {job.company_name} - {job.job_title}")
            except Exception as e:
                logger.warning(f"Failed to append application to CSV: {e}")

    def _save_company_to_yaml(self, filename: str, companies: List[Dict[str, str]]) -> None:
        """Save already viewed companies and their vacancies to a file"""
        output_file = self._define_output_file(filename)
        logger.info("Saving data about the vacancy in YAML")
        try:
            save_yaml_file(output_file, companies)
            logger.info("Data about the company and its vacancy successfully saved to YAML file")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(
                f"Error in saving information about viewed companies in YAML file\n{tb_str}"
            )
            raise Exception("Error in saving information about viewed companies in YAML file")

    def _load_companies_from_yaml(self, filename: str) -> Dict[str, List[dict]]:
        """Load file with already viewed companies and their vacancies.
        Handles both dict format (LinkedIn-style) and legacy list format (older Indeed output)."""
        output_file = self._define_output_file(filename)
        logger.info(f"Loading companies from YAML file: {output_file}")
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            logger.info(
                "Data about companies and their vacancies successfully loaded from YAML file"
            )
            if not data:
                return {}
            return data
        except FileNotFoundError:
            logger.warning(f"File {filename} not found, returning empty dict")
            return {}
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error loading information about viewed companies in YAML file\n{tb_str}")
            return {}

    def _save_interesting_job(self, job: Job, score: int, reasoning: str) -> None:
        """Save interesting job to a file"""
        logger.info("Saving interesting job to a file")
        interesting_job = JobInfo(
            job_title=job.job_title,
            company_name=job.company_name,
            url=job.url,
            interest_score=score,
            interest_reason=reasoning,
            skills=self.job_key_skills,
            llm_time_seconds=(
                self.llm_answerer_component.get_job_llm_time_seconds(job.url)
                if self.llm_answerer_component
                else 0.0
            ),
        )
        self.interesting_jobs.append(interesting_job)
        self.interesting_jobs = sorted(
            self.interesting_jobs, key=lambda x: int(x.interest_score), reverse=True
        )
        self._save_data_to_yaml(
            [job.model_dump() for job in self.interesting_jobs], "interesting_jobs.yaml"
        )
        logger.info("Interesting job successfully saved to a file")

    def _save_data_to_yaml(
        self,
        data: List[Dict[str, Any]] | Dict[str, Any] | str,
        filename: str,
        sort_keys: bool = True,
    ) -> None:
        """Save data to a file"""
        output_file = self._define_output_file(filename)
        logger.info(f"Saving data to file {filename}")
        try:
            save_yaml_file(output_file, data, sort_keys=sort_keys)
            logger.info(f"Data successfully saved to the file {filename}")
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in saving data to the file {filename}\n{tb_str}")
            raise Exception(f"Error in saving data to the file {filename}")

    def _load_data_from_yaml(self, filename: str) -> List[Dict[str, Any]] | Dict[str, Any] | str:
        """Load data from a file"""
        output_file = self._define_output_file(filename)
        logger.info(f"Loading data from file: {filename}")
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if (
                    filename == "answers.yaml" or filename == "interesting_jobs.yaml"
                ) and not isinstance(data, list):
                    raise ValueError(
                        f"The format of the file {filename} is incorrect, we expect a list"
                    )
            logger.info(f"Data successfully loaded from the file {filename}")
            return data
        except FileNotFoundError:
            logger.warning(f"The file {filename} was not found, returning an empty list")
            if filename == "answers.yaml" or filename == "interesting_jobs.yaml":
                return []
            return {}
        except Exception:
            tb_str = traceback.format_exc()
            logger.error(f"Error in loading the list of data from the file {filename}\n{tb_str}")
            raise Exception(f"Error in loading data from the file {filename}")

    def _is_blacklisted(self, company: str) -> bool:
        """Check if the company is in the blacklist"""
        if company in self.job_blacklist:
            logger.warning("The company is in the blacklist, skipping")
            return True
        return False

    def _match_seen_jobs(self, job: Job, companies: dict) -> Tuple[bool, str]:
        """Check if job matches any seen job in the given companies dictionary"""
        company_name = job.company_name
        job_title = job.job_title
        for comp in companies:
            if sanitize_text(company_name) == sanitize_text(comp):
                if self.apply_once_at_company and COLLECT_INFO_MODE is False:
                    logger.warning(
                        "The company has already been encountered and the setting is not to apply "
                        "again to the same company, skipping"
                    )
                    return (
                        True,
                        "The company has already been encountered and the setting is not to apply "
                        "again to the same company",
                    )
                for job_info in companies[comp]:
                    if job_title == job_info["job_title"]:
                        logger.warning("The vacancy has already been encountered, skipping")
                        return True, "The vacancy has already been encountered"
        return False, ""

    def _already_applied_to_position(self, job: Job) -> bool:
        """True if we have already successfully applied to this exact position.

        Match is per-position: same company AND same job title. Different
        positions at the same company are allowed (only the specific
        title/company pair is blacklisted). History comes from success.yaml,
        which is also updated in-memory during the run, so this guards against
        re-encountering the same position both across runs and within one run.

        The block is time-limited: an application only counts while it is newer
        than REAPPLY_BLACKLIST_DAYS (by its recorded ``executed_at`` date).
        Older entries age out (FIFO by application date) so the position becomes
        eligible to apply to again. REAPPLY_BLACKLIST_DAYS <= 0 disables the
        time limit and blocks forever.
        """
        company = sanitize_text(job.company_name)
        title = sanitize_text(job.job_title)
        cutoff = (
            datetime.now() - timedelta(days=REAPPLY_BLACKLIST_DAYS)
            if REAPPLY_BLACKLIST_DAYS and REAPPLY_BLACKLIST_DAYS > 0
            else None
        )
        for saved_company, saved_jobs in self.success_companies.items():
            if sanitize_text(saved_company) != company:
                continue
            for saved_job in saved_jobs:
                if sanitize_text(saved_job.get("job_title", "")) != title:
                    continue
                if self._application_is_active(saved_job.get("executed_at"), cutoff):
                    return True
        return False

    @staticmethod
    def _application_is_active(executed_at: Any, cutoff: datetime | None) -> bool:
        """Whether a saved application still blocks re-apply given the cutoff.

        cutoff is None when the time limit is disabled (always active). A
        missing or unparseable date is treated as still active - we would
        rather skip than risk re-applying to something we cannot date.
        """
        if cutoff is None:
            return True
        if not executed_at:
            return True
        try:
            applied_at = datetime.fromisoformat(str(executed_at))
        except ValueError:
            return True
        return applied_at >= cutoff

    def _job_is_already_seen(self, job: Job) -> Tuple[bool, str]:
        """Check if we have already applied to this vacancy.

        COLLECT_INFO_MODE deduplicates against the interesting-jobs list to
        avoid double-counting stats. In normal apply mode we skip a vacancy
        only when we have already successfully applied to the same position
        (same company + same title) - LinkedIn's own "Applied" state is not a
        reliable cross-run guard. Different positions at the same company are
        still applied to.
        """
        if COLLECT_INFO_MODE is True:
            company_name = job.company_name
            job_title = job.job_title
            my_companies = self.interesting_jobs
            for job_info in my_companies:
                if company_name == job_info.company_name and job_title == job_info.job_title:
                    logger.warning("The vacancy has already been encountered, skipping")
                    return True, "The vacancy has already been encountered"
            return False, ""

        if self._already_applied_to_position(job):
            logger.warning(
                "Already applied to this position at this company, skipping: "
                f"{job.job_title} at {job.company_name}"
            )
            return True, "Already applied to this position at this company"
        return False, ""

    def _check_the_previous_apply_number(self) -> int:
        """
        Check if there were applications without a completed search.
        If yes, return the number of applications
        """
        logger.info("Checking the time of the last application")
        if self.cache.last_apply:
            last_apply = self.cache.get_last_apply_datetime()
        else:
            return 0
        if (datetime.now() - last_apply).total_seconds() < 59 * 60:
            return self.cache.success_applies_num
        return 0

    def _load_cache(self) -> JobManagerCache:
        """Load cache from file"""
        try:
            with open(LAST_RUN_FILE, "r") as f:
                cache = yaml.safe_load(f) or {}
                return JobManagerCache(**cache)
        except Exception:
            logger.warning("Could not load cache from file")
            return JobManagerCache()

    def _collect_job_info(
        self, company_job_title: str, company_name: str, job_link: str, reason: str
    ) -> None:
        """Add a skipped vacancy to jobs_no_info for inclusion in the Telegram report"""
        job_info = JobInfo(
            job_title=company_job_title,
            company_name=company_name,
            url=job_link,
            skip_reason=reason,
        )
        self.jobs_no_info.append(job_info.model_dump())

    def resume_improvement_recommendations(self) -> None:
        """Generate LLM resume improvement advice and save to resume_recommendations.txt"""
        resume_recommendations_file = self._define_output_file("resume_recommendations.txt")
        try:
            with open(resume_recommendations_file, "r", encoding="utf-8") as f:
                self.resume_recommendations = f.read()
        except FileNotFoundError:
            self.resume_recommendations = ""
        if not self.resume_recommendations:
            logger.info("Generating resume improvement recommendations")
            self.resume_recommendations = (
                self.llm_answerer_component.resume_improvement_recommendations()
            )
            self.resume_recommendations = self.resume_anonymizer.deanonymize_text(
                self.resume_recommendations
            )
            with open(resume_recommendations_file, "w", encoding="utf-8") as f:
                f.write(self.resume_recommendations)

    def check_the_last_search_time(self) -> bool:
        """
        Check if the job search was started not earlier than 24 hours after the previous start.
        Or check if the last application was less than an hour ago
        This means that the application was forcibly restarted.
        """
        logger.info(
            "Checking if the job search was started not earlier than 24 hours after the previous start"
        )
        if self.cache.last_run:
            last_run = self.cache.get_last_run_datetime()
        else:
            return True
        if (
            datetime.now() - last_run
        ).total_seconds() >= 60 * 60 * 24 or self.previous_apply_number > 0:
            return True
        return False

    def _finalize_run_summary(self, result: str) -> None:
        """Log an end-of-run metrics summary and show it in a desktop pop-up.

        Always logs. The pop-up is shown only on Windows interactive runs - it is
        skipped when launched from the dashboard (which has its own live UI) and
        can be disabled entirely with SHOW_RUN_SUMMARY_POPUP. Best-effort: any
        failure here must never break the end-of-run flow.
        """
        try:
            counter = getattr(self, "results_counter", {}) or {}
            applied = counter.get("Success", 0)
            skipped = counter.get("Skip", 0)
            failed = counter.get("Error", 0)

            started_at = getattr(self, "run_started_at", None)
            duration = None
            if started_at:
                elapsed = int((datetime.now() - started_at).total_seconds())
                hours, remainder = divmod(elapsed, 3600)
                minutes, seconds = divmod(remainder, 60)
                duration = f"{hours}h {minutes:02d}m" if hours else f"{minutes}m {seconds:02d}s"

            success_this_run = self.success_applies_num - self.previous_apply_number
            if success_this_run >= self.max_applies_num:
                reason = f"Reached max applications ({self.max_applies_num})"
            elif result == "Limit":
                reason = "Stopped early (LinkedIn limit / safety breaker)"
            elif result == "Error":
                reason = "Stopped after repeated errors"
            else:
                reason = "Finished - no more matching jobs"

            metrics = {
                "applied": applied,
                "skipped": skipped,
                "failed": failed,
                "discovered": getattr(self, "total_discovered_jobs", 0),
                "processed": getattr(self, "applies_num", 0),
                "duration": duration,
                "reason": reason,
            }
            body = build_run_summary(metrics)
            title = f"{JOB_SITE.capitalize()} run finished"
            logger.info(f"Run summary:\n{title}\n{body}")

            if not SHOW_RUN_SUMMARY_POPUP:
                return
            # Dashboard-launched runs are headless/background and have their own UI.
            if os.environ.get("DASHBOARD_RUN_ID"):
                return
            show_summary_popup(title, body)
        except Exception as e:
            logger.warning(f"Failed to build run summary: {e}")

    async def send_report(self, result: str) -> None:
        """Send Telegram report after the application run completes"""
        # Show the metrics pop-up for every completed run, before the early
        # returns below (which skip the Telegram report in some modes).
        self._finalize_run_summary(result)
        if TEST_MODE or COLLECT_INFO_MODE or result == "Error":
            return
        if self.previous_apply_number >= self.success_applies_num:
            return
        try:
            logger.info("Sending a report about the work done in Telegram")
            bot = TelegramReportSender()
            await bot.send_telegram_report(
                self.email,
                self.resume,
                self.success_applies_num,
                self.jobs_no_info,
                self.skill_stat,
                self.resume_recommendations,
                self.resume_anonymizer,
            )
            self._write_the_last_search_time()
        except Exception as e:
            logger.warning(f"Failed to send Telegram report: {e}")

    async def _handle_apply_result(
        self,
        apply_result: Tuple[str, str],
        job: Job,
        evaluation: Dict[str, Any] | None = None,
    ) -> None:
        """Handle the result of a job application attempt"""
        result, _ = apply_result
        emit_event(
            "job_result",
            f"Job result: {result}",
            result=result.lower(),
            job_title=job.job_title,
            company_name=job.company_name,
            url=job.url,
            submitted_resume_path=(evaluation or {}).get("submitted_resume_path"),
        )
        self.applies_num += 1
        # Tally per-result counts for the end-of-run summary (self-healing if
        # set_parameters has not run, e.g. in some unit tests).
        self.results_counter = getattr(self, "results_counter", {})
        self.results_counter[result] = self.results_counter.get(result, 0) + 1
        if result != "Limit" and COLLECT_INFO_MODE is False:
            self._save_company(job, apply_result, {"url": job.url}, evaluation=evaluation)
        if result == "Success":
            self.success_applies_num += 1
            self.total_applies_num += 1
            self.cache.success_applies_num = self.success_applies_num
            self.cache.total_applies_num = self.total_applies_num
            self.cache.update_last_apply()
            self._write_the_last_search_time()
        elif result == "Error":
            self.error_num += 1

    def _write_the_last_search_time(self) -> None:
        """Write the time of the last job search"""
        save_yaml_file(LAST_RUN_FILE, self.cache.model_dump())
