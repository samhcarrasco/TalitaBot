"""Tests for open-ended/essay free-text detection.

Writing prompts (cover letters, "why do you want to work here", "tell us about
yourself", etc.) are never auto-written: optional ones are left blank, mandatory
ones skip the whole job. Short factual fields must NOT be classified as writing
prompts, or the bot would skip nearly every application.
"""

import pytest

from src.job_manager.linkedin.easy_applier_linkedin import LinkedInEasyApplier


WRITING_PROMPTS = [
    "Why would you like to work here?",
    "Why do you want to work for us?",
    "Tell us about yourself",
    "Cover letter",
    "Please provide a cover letter",
    "What excites you about this role?",
    "Describe your experience with cash forecasting",
    "In your own words, why are you a good fit?",
    "Additional information",
    "What motivates you?",
    "Why this company?",
    "Message to the hiring manager",
    "Tell us why you are interested",
    "Please explain any gaps in your employment",
    "What are you looking for in your next role?",
]

FACTUAL_FIELDS = [
    "City",
    "What is your notice period?",
    "How did you hear about us?",
    "Years of experience",
    "Desired salary",
    "LinkedIn profile URL",
    "Highest level of education",
    "Are you authorized to work in the US?",
    "Phone number",
    "Current location",
    "Earliest start date",
    "What is your current job title?",
]


@pytest.mark.parametrize("text", WRITING_PROMPTS)
def test_writing_prompts_are_detected(text):
    assert LinkedInEasyApplier._looks_like_writing_prompt(text) is True


@pytest.mark.parametrize("text", FACTUAL_FIELDS)
def test_factual_fields_are_not_writing_prompts(text):
    assert LinkedInEasyApplier._looks_like_writing_prompt(text) is False


def test_empty_text_is_not_a_writing_prompt():
    assert LinkedInEasyApplier._looks_like_writing_prompt("") is False
    assert LinkedInEasyApplier._looks_like_writing_prompt(None) is False
