"""Smoke test for the weekly-analysis system prompt.

We can't unit-test prompt *quality* — that requires real LLM runs against
real digests. What we can test is that the prompt file exists, parses, and
contains the load-bearing rules and section headings the runner depends on.
The rest is real-output iteration after we run on a past week.
"""

from __future__ import annotations

from pathlib import Path

PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "src" / "kcp_structgen" / "analysis" / "prompts" / "weekly_analysis.md"
)


def test_prompt_file_exists():
    assert PROMPT_PATH.is_file(), f"prompt missing at {PROMPT_PATH}"


def test_prompt_is_not_empty():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert len(text) > 500, "prompt suspiciously short"


def test_prompt_has_required_section_headings():
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for heading in (
        "## This week's headlines",
        "## Events",
        "## OI themes",
        "## Flow highlights",
        "## Desk client activity",
        "## Watch for next week",
    ):
        assert heading in text, f"missing required heading: {heading}"


def test_prompt_states_no_compute_rule():
    """The 'never compute numbers' rule must be in the prompt or the
    Python-owns-math contract is unenforced."""
    text = PROMPT_PATH.read_text(encoding="utf-8").lower()
    assert "never compute" in text or "do not compute" in text


def test_prompt_states_no_invented_strikes_rule():
    text = PROMPT_PATH.read_text(encoding="utf-8").lower()
    assert "never invent strike" in text or "do not invent strike" in text


def test_prompt_mentions_commentary_input():
    """Verifies the prompt actually teaches the LLM about daily_commentary."""
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert "daily_commentary" in text
    assert "headlines" in text.lower()
