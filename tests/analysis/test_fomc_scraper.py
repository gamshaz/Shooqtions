"""Tests for fomc_scraper.

Network is mocked everywhere. The `claude -p` call in `summarise_tone` is
mocked too — we test the orchestration, not real LLM output.
"""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from kcp_structgen.analysis.fomc_scraper import (
    FOMCScraperError,
    _build_tone_input,
    _extract_statement_body,
    _statement_url,
    _strip_fences,
    attach_tone_summary,
    diff_statements,
    find_previous_meeting,
    get_fomc_statement,
    summarise_tone,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_HTML_PATH = FIXTURES_DIR / "sample_fomc_statement.html"


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def test_statement_url_format():
    url = _statement_url(date(2026, 9, 16))
    assert url == "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"


# ---------------------------------------------------------------------------
# Hardcoded calendar
# ---------------------------------------------------------------------------

def test_find_previous_meeting_within_year():
    prev = find_previous_meeting(date(2026, 6, 17))
    assert prev == date(2026, 4, 29)


def test_find_previous_meeting_across_year_boundary():
    """Jan 2027 meeting → previous is December 2026."""
    prev = find_previous_meeting(date(2027, 1, 27))
    assert prev == date(2026, 12, 16)


def test_find_previous_meeting_none_before_calendar():
    """Date before our earliest hardcoded meeting → None."""
    assert find_previous_meeting(date(2025, 1, 1)) is None


def test_find_previous_meeting_exact_match_excluded():
    """If curr_date *is* a meeting day, previous is the one before it,
    not the meeting itself."""
    prev = find_previous_meeting(date(2026, 4, 29))
    assert prev == date(2026, 3, 18)


# ---------------------------------------------------------------------------
# HTML body extraction
# ---------------------------------------------------------------------------

def test_extract_statement_body_from_fixture():
    html = SAMPLE_HTML_PATH.read_text(encoding="utf-8")
    text = _extract_statement_body(html)

    # Key substantive paragraphs are present
    assert "lower the target range" in text
    assert "4-1/4 to 4-1/2 percent" in text
    assert "Voting for the monetary policy action were" in text

    # Skip-tag content must NOT be present
    assert "Skip nav links" not in text
    assert "Footer disclaimer text" not in text
    assert "_fed_tracker" not in text
    assert "Sidebar with unrelated nav" not in text


def test_extract_statement_body_empty_html():
    assert _extract_statement_body("") == ""


def test_extract_statement_body_no_article_div():
    """If the HTML lacks any of the recognised statement containers,
    body extraction returns empty (caller treats as failure)."""
    html = "<html><body><p>Nothing in an article div</p></body></html>"
    assert _extract_statement_body(html).strip() == ""


# ---------------------------------------------------------------------------
# Caching + fetch
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_get_statement_cache_hit_no_network(tmp_path):
    """If the cache has the file, no HTTP call is made."""
    meeting = date(2026, 9, 16)
    cache = tmp_path / f"{meeting.isoformat()}.txt"
    cache.write_text("cached statement text", encoding="utf-8")

    with patch("urllib.request.urlopen") as mock_url:
        result = get_fomc_statement(meeting, cache_dir=tmp_path)
        mock_url.assert_not_called()

    assert result == "cached statement text"


def test_get_statement_live_fetch_writes_cache(tmp_path):
    meeting = date(2026, 9, 16)
    html = SAMPLE_HTML_PATH.read_text(encoding="utf-8")

    with patch("urllib.request.urlopen", return_value=_FakeResp(html)):
        result = get_fomc_statement(meeting, cache_dir=tmp_path)

    assert result is not None
    assert "lower the target range" in result

    # Cache written
    cache = tmp_path / "2026-09-16.txt"
    assert cache.is_file()
    assert cache.read_text(encoding="utf-8") == result


def test_get_statement_http_error_returns_none(tmp_path):
    import urllib.error
    meeting = date(2026, 9, 16)

    def _raise(*a, **kw):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    with patch("urllib.request.urlopen", side_effect=_raise):
        assert get_fomc_statement(meeting, cache_dir=tmp_path) is None


def test_get_statement_empty_body_returns_none(tmp_path):
    """If the page exists but the extractor can't find a statement, treat
    as failure (don't cache empty text)."""
    meeting = date(2026, 9, 16)
    bad_html = "<html><body><div>nothing useful here</div></body></html>"

    with patch("urllib.request.urlopen", return_value=_FakeResp(bad_html)):
        assert get_fomc_statement(meeting, cache_dir=tmp_path) is None

    # Cache must NOT be written for empty extraction
    assert not (tmp_path / f"{meeting.isoformat()}.txt").exists()


def test_get_statement_allow_network_false_no_cache(tmp_path):
    """allow_network=False and no cache → None, no scrape attempted."""
    with patch("urllib.request.urlopen") as mock_url:
        result = get_fomc_statement(date(2026, 9, 16),
                                    cache_dir=tmp_path,
                                    allow_network=False)
        mock_url.assert_not_called()
    assert result is None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def test_diff_statements_basic():
    prev = "Sentence A. Sentence B. Sentence C."
    curr = "Sentence A. Sentence D. Sentence C."
    diff = diff_statements(prev, curr)
    assert "Sentence A." in diff["kept"]
    assert "Sentence C." in diff["kept"]
    assert "Sentence D." in diff["added"]
    assert "Sentence B." in diff["removed"]


def test_diff_statements_empty_inputs():
    assert diff_statements("", "") == {"kept": [], "added": [], "removed": []}


def test_diff_statements_identical():
    text = "Same. Identical. Statement."
    diff = diff_statements(text, text)
    assert diff["added"] == []
    assert diff["removed"] == []
    assert len(diff["kept"]) == 3


# ---------------------------------------------------------------------------
# Tone-input builder
# ---------------------------------------------------------------------------

def test_build_tone_input_layout():
    out = _build_tone_input(
        date(2026, 7, 29), date(2026, 9, 16),
        "prev text", "curr text",
    )
    assert "PREVIOUS STATEMENT (2026-07-29):" in out
    assert "CURRENT STATEMENT (2026-09-16):" in out
    assert out.index("PREVIOUS") < out.index("CURRENT")


# ---------------------------------------------------------------------------
# Fence stripping
# ---------------------------------------------------------------------------

def test_strip_fences_no_fence():
    assert _strip_fences("plain text") == "plain text"


def test_strip_fences_with_lang_fence():
    raw = "```text\nthe summary text\n```"
    assert _strip_fences(raw) == "the summary text"


def test_strip_fences_plain_fence():
    raw = "```\nthe summary\n```"
    assert _strip_fences(raw) == "the summary"


# ---------------------------------------------------------------------------
# summarise_tone — claude -p mocked
# ---------------------------------------------------------------------------

def _mock_run(stdout="A clean tone summary.", returncode=0):
    class _R:
        def __init__(self, out, rc):
            self.stdout = out
            self.stderr = ""
            self.returncode = rc
    return _R(stdout, returncode)


def test_summarise_tone_calls_cli_and_returns_text():
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run("Tone shifted hawkish.")):
        out = summarise_tone(
            "previous text", "current text",
            date(2026, 7, 29), date(2026, 9, 16),
        )
    assert out == "Tone shifted hawkish."


def test_summarise_tone_strips_fences():
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run",
               return_value=_mock_run("```text\nFenced summary.\n```")):
        out = summarise_tone("p", "c", date(2026, 7, 29), date(2026, 9, 16))
    assert out == "Fenced summary."


def test_summarise_tone_returns_none_when_cli_missing():
    with patch("shutil.which", return_value=None):
        out = summarise_tone("p", "c", date(2026, 7, 29), date(2026, 9, 16))
    assert out is None


def test_summarise_tone_returns_none_on_timeout():
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=120)
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", side_effect=_raise):
        out = summarise_tone("p", "c", date(2026, 7, 29), date(2026, 9, 16))
    assert out is None


def test_summarise_tone_returns_none_on_nonzero_exit():
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run("error", returncode=1)):
        out = summarise_tone("p", "c", date(2026, 7, 29), date(2026, 9, 16))
    assert out is None


def test_summarise_tone_returns_none_on_empty_stdout():
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run("   ")):
        out = summarise_tone("p", "c", date(2026, 7, 29), date(2026, 9, 16))
    assert out is None


# ---------------------------------------------------------------------------
# attach_tone_summary — end-to-end
# ---------------------------------------------------------------------------

def _seed_caches(cache_dir: Path, curr_date: date, prev_date: date) -> None:
    """Write minimal statement texts to skip the HTTP path entirely."""
    (cache_dir / f"{curr_date.isoformat()}.txt").write_text(
        "Current statement. Rate cut delivered.", encoding="utf-8")
    (cache_dir / f"{prev_date.isoformat()}.txt").write_text(
        "Previous statement. Held rates steady.", encoding="utf-8")


def test_attach_tone_summary_happy_path(tmp_path):
    curr = date(2026, 9, 16)
    prev = date(2026, 7, 29)
    _seed_caches(tmp_path, curr, prev)
    event = {"matcher": "FOMC", "date": curr.isoformat(),
             "event_name": "FOMC Rate Decision"}

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run("Dovish cut, in line.")):
        result = attach_tone_summary(event, cache_dir=tmp_path)

    assert result is event   # returns same object for chaining
    assert event["fomc_tone_summary"] == "Dovish cut, in line."


def test_attach_tone_summary_non_fomc_event_untouched():
    event = {"matcher": "CPI", "date": "2026-09-18", "event_name": "CPI YoY"}
    out = attach_tone_summary(event)
    assert "fomc_tone_summary" not in out


def test_attach_tone_summary_no_previous_meeting(tmp_path):
    """Event date before our hardcoded calendar → no previous → tone None."""
    event = {"matcher": "FOMC", "date": "2025-01-15"}
    out = attach_tone_summary(event, cache_dir=tmp_path)
    assert out["fomc_tone_summary"] is None


def test_attach_tone_summary_missing_curr_statement(tmp_path):
    """If get_fomc_statement returns None (no cache + network disabled),
    tone is None and no LLM call is made."""
    event = {"matcher": "FOMC", "date": "2026-09-16"}

    with patch("subprocess.run") as mock_run:
        out = attach_tone_summary(event,
                                  cache_dir=tmp_path,
                                  allow_network=False)
        mock_run.assert_not_called()
    assert out["fomc_tone_summary"] is None


def test_attach_tone_summary_bad_date_returns_none():
    event = {"matcher": "FOMC", "date": "not-a-date"}
    out = attach_tone_summary(event)
    assert out["fomc_tone_summary"] is None


def test_attach_tone_summary_missing_date_returns_none():
    event = {"matcher": "FOMC"}
    out = attach_tone_summary(event)
    assert out["fomc_tone_summary"] is None
