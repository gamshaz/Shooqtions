"""FOMC tone-summary attachment.

Scrapes the FOMC statement HTML from federalreserve.gov, diffs against the
previous meeting's statement (pulled from cache where possible), and calls
`claude -p` once for a 2-3 sentence rates-strategist tone summary that
attaches to the FOMC event row as `fomc_tone_summary`.

Best-effort enrichment — every failure path returns None and the runner
flags "FOMC tone unavailable" in the digest warnings. The rest of Layer 2
still produces a complete digest without it.

Hardcoded 2026-2027 meeting calendar lives in this module (no external
calendar dependency). When 2028 schedule lands, extend `_FOMC_MEETINGS`.
"""

from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "fomc_statements"

HTTP_TIMEOUT_S = 30
CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT_S = 120

URL_TEMPLATE = "https://www.federalreserve.gov/newsevents/pressreleases/monetary{ymd}a.htm"

# Hardcoded FOMC meeting dates. Source: federalreserve.gov calendar.
# 2026 and 2027 meetings — eight per year on the standard cycle.
_FOMC_MEETINGS: dict[int, list[date]] = {
    2026: [
        date(2026, 1, 28),
        date(2026, 3, 18),
        date(2026, 4, 29),
        date(2026, 6, 17),
        date(2026, 7, 29),
        date(2026, 9, 16),
        date(2026, 10, 28),
        date(2026, 12, 16),
    ],
    2027: [
        date(2027, 1, 27),
        date(2027, 3, 17),
        date(2027, 4, 28),
        date(2027, 6, 16),
        date(2027, 7, 28),
        date(2027, 9, 15),
        date(2027, 10, 27),
        date(2027, 12, 15),
    ],
}


TONE_PROMPT = """\
You are a rates strategist / economist analysing FOMC communications for a
STIR options sales desk. Below are two consecutive FOMC statements.

Write a 2-3 sentence summary that calls out, in this order:

  1. Was the rate action (hike / cut / hold) in line with consensus, or a
     surprise? Quantify if known.
  2. How did the statement language shift: more hawkish, more dovish, or
     unchanged? Cite the specific phrases added or removed that move it.
  3. Anything else materially new (forward guidance, balance sheet, dot-plot
     reference, anomalies) worth flagging to a rates trader.

Reply in plain text. No markdown, no preamble, no quoted text. First
character is a letter; do not start with "Here is" or "Summary:".
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FOMCScraperError(RuntimeError):
    """Internal failure during scrape / parse / LLM call. Caller-facing
    public API converts this to `None` so the rest of Layer 2 keeps working."""


# ---------------------------------------------------------------------------
# Meeting calendar
# ---------------------------------------------------------------------------

def find_previous_meeting(curr_date: date) -> date | None:
    """Return the FOMC meeting immediately before `curr_date` from the
    hardcoded calendar, or `None` if no earlier meeting is in scope.
    """
    all_meetings: list[date] = []
    for year in sorted(_FOMC_MEETINGS):
        all_meetings.extend(_FOMC_MEETINGS[year])
    earlier = [m for m in all_meetings if m < curr_date]
    return max(earlier) if earlier else None


# ---------------------------------------------------------------------------
# URL + HTML parsing
# ---------------------------------------------------------------------------

def _statement_url(meeting_date: date) -> str:
    return URL_TEMPLATE.format(ymd=meeting_date.strftime("%Y%m%d"))


class _StatementExtractor(HTMLParser):
    """Walk Fed press-release HTML and extract the statement body text.

    The Fed wraps statements in `<div id="article">...</div>` or
    `<div class="col-xs-12 col-sm-8 col-md-8">` depending on template
    vintage. We capture text between common markers and skip script/style.
    """

    _INTERESTING_TAGS = {"p", "h1", "h2", "h3"}
    _SKIP_TAGS = {"script", "style", "nav", "header", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self._depth_in_article = 0
        self._skip_depth = 0
        self._buf: list[str] = []
        self._in_interesting = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag.lower() == "div":
            div_id = (attrs_d.get("id") or "").lower()
            div_class = (attrs_d.get("class") or "").lower()
            if div_id == "article" or "col-md-8" in div_class or "col-sm-8" in div_class:
                self._depth_in_article += 1
        if tag.lower() in self._INTERESTING_TAGS and self._depth_in_article > 0:
            self._in_interesting = True

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag.lower() == "div" and self._depth_in_article > 0:
            self._depth_in_article -= 1
        if tag.lower() in self._INTERESTING_TAGS:
            self._in_interesting = False
            self._buf.append("\n")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        if self._in_interesting and self._depth_in_article > 0:
            self._buf.append(data)

    @property
    def text(self) -> str:
        return _normalise_whitespace("".join(self._buf))


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace, strip per line, drop empty lines."""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_statement_body(html: str) -> str:
    parser = _StatementExtractor()
    parser.feed(html)
    return parser.text


# ---------------------------------------------------------------------------
# Statement fetch + cache
# ---------------------------------------------------------------------------

def _cache_path(meeting_date: date, cache_dir: Path) -> Path:
    return cache_dir / f"{meeting_date.isoformat()}.txt"


def _read_cache(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _write_cache(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fetch_html(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise FOMCScraperError(f"HTTP {exc.code} {exc.reason} at {url}") from exc
    except urllib.error.URLError as exc:
        raise FOMCScraperError(f"network error: {exc.reason}") from exc

    # Fed pages declare charset in headers but utf-8 is safe across the
    # archive we care about.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def get_fomc_statement(meeting_date: date,
                       cache_dir: Path | None = None,
                       *,
                       allow_network: bool = True) -> str | None:
    """Return the plain-text body of the FOMC statement for `meeting_date`.

    Resolution order:
      1. Read `<cache_dir>/<date>.txt` if present.
      2. Fetch from federalreserve.gov, parse, write cache, return.

    Returns `None` on any failure (HTTP error, parser produced empty text,
    network disabled and no cache).
    """
    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    cache = _cache_path(meeting_date, cache_dir)
    cached = _read_cache(cache)
    if cached is not None:
        return cached

    if not allow_network:
        return None

    try:
        html = _fetch_html(_statement_url(meeting_date))
        text = _extract_statement_body(html)
    except FOMCScraperError:
        return None

    if not text.strip():
        return None

    _write_cache(cache, text)
    return text


# ---------------------------------------------------------------------------
# Diff + tone summary
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    """Best-effort sentence split. Keeps order; trims whitespace."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def diff_statements(prev: str, curr: str) -> dict:
    """Sentence-level diff: which sentences are kept, added, removed.

    Order is preserved for `added` and `removed` (from their source). The
    LLM gets all three lists — the diff is a hint, not a constraint.
    """
    prev_sents = _split_sentences(prev)
    curr_sents = _split_sentences(curr)
    prev_set = set(prev_sents)
    curr_set = set(curr_sents)
    return {
        "kept":    [s for s in curr_sents if s in prev_set],
        "added":   [s for s in curr_sents if s not in prev_set],
        "removed": [s for s in prev_sents if s not in curr_set],
    }


def _build_tone_input(prev_date: date, curr_date: date,
                      prev_text: str, curr_text: str) -> str:
    """Compose the user-side text passed to `claude -p`."""
    return (
        f"PREVIOUS STATEMENT ({prev_date.isoformat()}):\n{prev_text}\n\n"
        f"CURRENT STATEMENT ({curr_date.isoformat()}):\n{curr_text}\n"
    )


def _strip_fences(text: str) -> str:
    """Defensive: strip a leading/trailing ```text``` fence if the model
    ignored the prompt and wrapped its reply."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    return s.strip()


def summarise_tone(prev_text: str, curr_text: str,
                   prev_date: date, curr_date: date) -> str | None:
    """Call `claude -p` once for the rates-strategist tone summary.

    Returns the summary text, or `None` if the CLI is missing, times out,
    or returns empty.
    """
    import shutil
    if shutil.which(CLAUDE_CLI) is None:
        return None

    user_input = _build_tone_input(prev_date, curr_date, prev_text, curr_text)
    try:
        result = subprocess.run(
            [CLAUDE_CLI, "-p", "--append-system-prompt", TONE_PROMPT],
            input=user_input,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
            encoding="utf-8",
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    out = _strip_fences(result.stdout or "")
    return out or None


# ---------------------------------------------------------------------------
# Public entry — attach tone to an event
# ---------------------------------------------------------------------------

def attach_tone_summary(fomc_event: dict,
                        cache_dir: Path | None = None,
                        *,
                        allow_network: bool = True) -> dict:
    """Mutate `fomc_event` in place: set `fomc_tone_summary` if we can
    produce one. Returns the same event for chaining.

    On any failure (missing previous meeting, scraper fails, LLM fails,
    cache+network both unavailable), `fomc_tone_summary` is left unset
    (or set to `None` if already present). The runner adds a warning;
    the rest of the digest is unaffected.
    """
    if fomc_event.get("matcher") != "FOMC":
        return fomc_event

    raw_date = fomc_event.get("date")
    if not raw_date:
        fomc_event["fomc_tone_summary"] = None
        return fomc_event

    try:
        curr_date = date.fromisoformat(str(raw_date).split("T", 1)[0].split(" ", 1)[0])
    except ValueError:
        fomc_event["fomc_tone_summary"] = None
        return fomc_event

    prev_date = find_previous_meeting(curr_date)
    if prev_date is None:
        fomc_event["fomc_tone_summary"] = None
        return fomc_event

    curr_text = get_fomc_statement(curr_date, cache_dir, allow_network=allow_network)
    prev_text = get_fomc_statement(prev_date, cache_dir, allow_network=allow_network)
    if not curr_text or not prev_text:
        fomc_event["fomc_tone_summary"] = None
        return fomc_event

    summary = summarise_tone(prev_text, curr_text, prev_date, curr_date)
    fomc_event["fomc_tone_summary"] = summary  # may be None if LLM failed
    return fomc_event
