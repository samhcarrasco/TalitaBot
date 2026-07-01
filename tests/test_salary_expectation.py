"""Tests for Talita's salary-expectation policy in BaseEasyApplier.

Policy:
- Listing advertises a salary range -> ask for its high end (SALARY_FLOOR is 0, no floor).
- No range, single-value (numeric) field -> DEFAULT_SINGLE_SALARY.
- No range, range-accepting (non-numeric) field -> "DEFAULT_RANGE_LOW - DEFAULT_RANGE_HIGH".
- Hourly-looking ranges are ignored so we never anchor to an hourly rate.
"""

from types import SimpleNamespace

import pytest

from src.job_manager.easy_applier import BaseEasyApplier


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$90,000 - $130,000 per year", 130000),
        ("Pay range: 90k to 130k", 130000),
        ("$120,000–$150,000", 150000),  # en-dash separator
        ("Compensation 80,000 - 85,000 annually", 85000),
        ("$45 - $60 / hour", None),  # hourly -> ignored
        ("Salary up to $140,000", None),  # single value, not a range
        ("No salary listed here", None),
        ("", None),
    ],
)
def test_parse_salary_high_end(text, expected):
    assert BaseEasyApplier._parse_salary_high_end(text) == expected


def _answer(salary_text: str | None, is_numeric: bool) -> str:
    """Call the instance method with a minimal fake `self` carrying a job."""
    fake_self = SimpleNamespace(
        current_job=SimpleNamespace(salary_range=None, job_description=salary_text or ""),
        SALARY_FLOOR=BaseEasyApplier.SALARY_FLOOR,
        DEFAULT_SINGLE_SALARY=BaseEasyApplier.DEFAULT_SINGLE_SALARY,
        DEFAULT_RANGE_LOW=BaseEasyApplier.DEFAULT_RANGE_LOW,
        DEFAULT_RANGE_HIGH=BaseEasyApplier.DEFAULT_RANGE_HIGH,
        _parse_salary_high_end=BaseEasyApplier._parse_salary_high_end,
    )
    return BaseEasyApplier._salary_expectation_answer(fake_self, is_numeric)


def test_listing_range_uses_high_end():
    assert _answer("$90,000 - $130,000", is_numeric=True) == "130000"


def test_low_range_not_floored():
    # SALARY_FLOOR is 0, so a low advertised range is answered as-is (no floor bump).
    assert _answer("$40,000 - $50,000", is_numeric=True) == "50000"


def test_no_range_single_value_field():
    assert _answer("No salary listed", is_numeric=True) == "70000"


def test_no_range_range_field():
    assert _answer("No salary listed", is_numeric=False) == "65,000 - 75,000"


def test_hourly_range_falls_back_to_single():
    assert _answer("$45 - $60 per hour", is_numeric=True) == "70000"


def test_missing_job_context_falls_back():
    fake_self = SimpleNamespace(
        current_job=None,
        SALARY_FLOOR=BaseEasyApplier.SALARY_FLOOR,
        DEFAULT_SINGLE_SALARY=BaseEasyApplier.DEFAULT_SINGLE_SALARY,
        DEFAULT_RANGE_LOW=BaseEasyApplier.DEFAULT_RANGE_LOW,
        DEFAULT_RANGE_HIGH=BaseEasyApplier.DEFAULT_RANGE_HIGH,
        _parse_salary_high_end=BaseEasyApplier._parse_salary_high_end,
    )
    assert BaseEasyApplier._salary_expectation_answer(fake_self, True) == "70000"
