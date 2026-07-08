"""Tests for the deterministic commute/relocation guard in BaseEasyApplier.

Policy: a question asking whether the candidate is comfortable/willing to
commute or relocate is always answered "Yes". Answering "No" silently
auto-rejects the application, so this must never be left to the LLM or a stale
cached answer. Negated phrasings (where "Yes" would be wrong) and non-yes/no
commute questions must NOT be forced.
"""

import pytest

from src.job_manager.easy_applier import BaseEasyApplier

detect = BaseEasyApplier._is_commute_relocation_question
pick_yes = BaseEasyApplier._pick_yes_option
yes_no_phrasing = BaseEasyApplier._looks_like_yes_no_phrasing


@pytest.mark.parametrize(
    "question",
    [
        "Are you comfortable commuting to this job's location?",
        "are you comfortable commuting to this jobs location?",  # sanitized form
        "Are you willing to relocate?",
        "Are you able to reliably commute to New York, NY?",
        "Are you willing to relocate for this position?",
        "Would you be able to commute to the office 3 days a week?",
        "Are you open to relocating?",
        "ARE YOU COMFORTABLE COMMUTING TO THIS JOB'S LOCATION?",  # case-insensitive
    ],
)
def test_detects_commute_relocation_questions(question):
    assert detect(question) is True


@pytest.mark.parametrize(
    "question",
    [
        # Negated phrasings where "Yes" would be the WRONG answer.
        "Is there anything preventing you from commuting to this location?",
        "Are you unable to commute to this location?",
        "Do you have any issues commuting to this location?",
        "Do you have any concerns about relocating?",
        "Would commuting to this location be a problem for you?",
        # Requests for money — "Yes" auto-rejects, like sponsorship.
        "Do you require relocation assistance?",
        "Samsara will not provide relocation assistance for this role. "
        "Do you require relocation assistance?",
        "Would you need relocation reimbursement?",
        # Unrelated questions.
        "What are your salary expectations?",
        "Are you legally authorized to work in the United States?",
        "Where are you located?",
        "",
    ],
)
def test_ignores_negated_and_unrelated_questions(question):
    assert detect(question) is False


@pytest.mark.parametrize(
    "options,expected",
    [
        (["Yes", "No"], "Yes"),
        (["yes", "no"], "yes"),  # radio labels arrive lowercased
        (["Select an option", "Yes", "No"], "Yes"),
        (["No", "Yes"], "Yes"),  # order-independent
        (["No, I cannot commute", "Yes, I can commute"], "Yes, I can commute"),
        (["No"], None),  # no affirmative option -> caller falls back
        # Non-yes/no commute question shapes -> caller falls back to the LLM.
        (["Under 30 minutes", "30-60 minutes", "Over an hour"], None),
        (["Car", "Public transit", "Walk"], None),
    ],
)
def test_pick_yes_option(options, expected):
    assert pick_yes(options) == expected


@pytest.mark.parametrize(
    "question,expected",
    [
        # Yes/no willingness phrasings -> a plain "Yes" is a valid text answer.
        ("Are you comfortable commuting to this job's location?", True),
        ("If located outside the posting location, are you open to relocation "
         "at your own expense?", True),
        ("Would you be willing to relocate?", True),
        # Open questions -> "Yes" would be nonsense; leave to the LLM.
        ("How long is your commute?", False),
        ("What is your expected commute time in minutes?", False),
    ],
)
def test_yes_no_phrasing(question, expected):
    assert yes_no_phrasing(question) is expected


def test_end_to_end_forced_answer_is_yes():
    """The exact LinkedIn stock question maps to the 'Yes' option regardless of
    whether it renders as radio, dropdown, or checkbox."""
    question = "are you comfortable commuting to this job's location?"
    options = ["yes", "no"]
    assert detect(question) is True
    assert pick_yes(options) == "yes"
