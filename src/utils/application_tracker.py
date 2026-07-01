"""
Tracks job applications in a persistent CSV file.

Two entry points:
  - append_application(row)        — called by the bot on each successful apply
  - update_application_status(...) — called by the Gmail agent when an email arrives

Matching strategy for updates: case-insensitive company_name required;
job_title narrows the match when provided. If a company has multiple open
applications the most recently applied one is updated.

Status vocabulary (the Gmail agent should use one of these strings):
  "Applied"                  — initial state set by the bot
  "Viewed"                   — recruiter opened the application
  "Phone Screen Scheduled"
  "Phone Screen Completed"
  "Interview Round N Scheduled"   — replace N with the round number
  "Interview Round N Completed"
  "Offer Received"
  "Rejected"
  "Withdrawn"
  "No Response"              — set manually or by a scheduled sweep
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.app_config import JOB_SITE
from config.constants import OUTPUT_DIR_INDEED, OUTPUT_DIR_LINKEDIN
from config.logger_config import logger

_OUTPUT_DIR = OUTPUT_DIR_LINKEDIN if JOB_SITE == "linkedin" else OUTPUT_DIR_INDEED
APPLICATIONS_CSV = Path(_OUTPUT_DIR) / "applications.csv"

CSV_FIELDS = [
    "applied_at",
    "company_name",
    "job_title",
    "location",
    "is_remote",
    "salary_range",
    "employment_type",
    "experience_level",
    "interest_score",
    # --- status columns (updated by the Gmail agent) ---
    "status",           # one of the status strings above
    "last_status_at",   # ISO timestamp of last status change
    "interview_date",   # scheduled date/time (ISO or human-readable from the email)
    "interview_round",  # integer round number
    "notes",            # agent-written summary extracted from the email
    # --- identifiers ---
    "submitted_resume_path",
    "job_id",
    "url",
]

_EMPTY_ROW_DEFAULTS = {f: "" for f in CSV_FIELDS}


def append_application(row: dict) -> None:
    """Write one row for a newly submitted application. Called by job_manager."""
    full_row = {**_EMPTY_ROW_DEFAULTS, "status": "Applied", **row}
    write_header = not APPLICATIONS_CSV.exists()
    APPLICATIONS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(APPLICATIONS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(full_row)


def update_application_status(
    company_name: str,
    status: str,
    job_title: Optional[str] = None,
    interview_date: str = "",
    interview_round: str = "",
    notes: str = "",
) -> int:
    """
    Update status columns for matching application row(s).

    Matches on company_name (case-insensitive); job_title further narrows
    when provided. If multiple rows still match, the most recently applied
    one is updated. Returns the number of rows updated.

    Designed to be called directly by the Gmail agent:

        from src.utils.application_tracker import update_application_status
        updated = update_application_status(
            company_name="Acme Corp",
            status="Interview Round 1 Scheduled",
            job_title="Software Engineer",
            interview_date="2026-06-15 10:00",
            interview_round="1",
            notes="Technical screen with hiring manager, 45 min via Zoom",
        )
    """
    if not APPLICATIONS_CSV.exists():
        logger.warning("applications.csv not found — no rows to update")
        return 0

    with open(APPLICATIONS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return 0

    co_lower = company_name.strip().lower()
    jt_lower = job_title.strip().lower() if job_title else None

    candidates = [
        r for r in rows
        if r.get("company_name", "").strip().lower() == co_lower
    ]
    if jt_lower:
        narrow = [r for r in candidates if r.get("job_title", "").strip().lower() == jt_lower]
        if narrow:
            candidates = narrow

    if not candidates:
        logger.warning(f"No application found for company='{company_name}' title='{job_title}'")
        return 0

    # Update the most recently applied match
    target = max(candidates, key=lambda r: r.get("applied_at", ""))
    now = datetime.now().isoformat(timespec="seconds")
    target["status"] = status
    target["last_status_at"] = now
    if interview_date:
        target["interview_date"] = interview_date
    if interview_round:
        target["interview_round"] = interview_round
    if notes:
        target["notes"] = notes

    with open(APPLICATIONS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    logger.info(f"Updated application status: {company_name} → {status}")
    return 1


def get_applications(company_name: Optional[str] = None) -> list[dict]:
    """Return all rows, optionally filtered by company_name (case-insensitive)."""
    if not APPLICATIONS_CSV.exists():
        return []
    with open(APPLICATIONS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if company_name:
        co_lower = company_name.strip().lower()
        rows = [r for r in rows if r.get("company_name", "").strip().lower() == co_lower]
    return rows
