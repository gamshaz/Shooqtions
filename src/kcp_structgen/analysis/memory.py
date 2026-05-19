"""Cross-week memory — read prior digests, write the current week.

Per spec §11 + §16: each weekly run persists three artefacts to
`data/weekly_digests/`:

  <week_label>.json           — the structured digest the LLM saw
  <week_label>_headlines.md   — bullet headlines extracted from the rundown
  <week_label>_rundown.md     — the full markdown rundown

The runner calls `load_prior_weeks()` at the start of a run to seed the
current digest's `prior_weeks` field, and `save_week_outputs()` at the end
to persist this week's results for next week's memory.

No size cap, no pruning. Disk is cheap; output quality matters more.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from .events_api import week_label

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MEMORY_DIR = REPO_ROOT / "data" / "weekly_digests"

HEADLINES_SECTION_RE = re.compile(
    r"##\s*This week's headlines\s*\n+(.*?)(?=\n##\s|\Z)",
    flags=re.DOTALL | re.IGNORECASE,
)
BULLET_LINE_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$", flags=re.MULTILINE)

MAX_HEADLINES = 5


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _digest_path(label: str, memory_dir: Path) -> Path:
    return memory_dir / f"{label}.json"


def _headlines_path(label: str, memory_dir: Path) -> Path:
    return memory_dir / f"{label}_headlines.md"


def _rundown_path(label: str, memory_dir: Path) -> Path:
    return memory_dir / f"{label}_rundown.md"


def _iso_week_offset(curr_week_d: date, weeks_back: int) -> date:
    """Return a date `weeks_back` ISO weeks before `curr_week_d`. Picks the
    Monday of that earlier week to keep the date stable inside that week."""
    iso_year, iso_week, _ = curr_week_d.isocalendar()
    target_monday = date.fromisocalendar(iso_year, iso_week, 1) - timedelta(weeks=weeks_back)
    return target_monday


# ---------------------------------------------------------------------------
# Headline extraction
# ---------------------------------------------------------------------------

def extract_headlines(rundown_md: str) -> list[str]:
    """Pull up to MAX_HEADLINES bullet lines from the rundown's
    `## This week's headlines` section.

    Returns an empty list if the section isn't present or has no bullets.
    Bullets are recognised as lines starting with `-` or `*`. Whitespace
    is trimmed. Cap at MAX_HEADLINES.
    """
    if not rundown_md:
        return []
    section = HEADLINES_SECTION_RE.search(rundown_md)
    if section is None:
        return []
    body = section.group(1)
    bullets = [m.group(1).strip() for m in BULLET_LINE_RE.finditer(body)]
    return bullets[:MAX_HEADLINES]


# ---------------------------------------------------------------------------
# Read — load prior weeks
# ---------------------------------------------------------------------------

def load_prior_weeks(curr_week_d: date,
                     n_weeks: int = 3,
                     memory_dir: Path | None = None) -> list[dict]:
    """Return up to `n_weeks` of prior weekly artefacts.

    Each entry: {'week': '<week_label>', 'digest': {...}, 'headlines': [...]}.

    Ordered newest-first (the week immediately before this one is index 0).
    Missing weeks are silently skipped — the first run ever returns [].
    """
    memory_dir = memory_dir or DEFAULT_MEMORY_DIR
    out: list[dict] = []
    for n in range(1, n_weeks + 1):
        prior_d = _iso_week_offset(curr_week_d, n)
        label = week_label(prior_d)
        digest_p = _digest_path(label, memory_dir)
        if not digest_p.is_file():
            continue
        try:
            digest = json.loads(digest_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Corrupt digest file — skip without crashing the run.
            continue

        headlines_p = _headlines_path(label, memory_dir)
        headlines: list[str] = []
        if headlines_p.is_file():
            text = headlines_p.read_text(encoding="utf-8")
            headlines = [m.group(1).strip() for m in BULLET_LINE_RE.finditer(text)]

        out.append({"week": label, "digest": digest, "headlines": headlines})
    return out


# ---------------------------------------------------------------------------
# Write — save this week's artefacts
# ---------------------------------------------------------------------------

def save_week_outputs(week_d: date,
                      digest: dict,
                      headlines: list[str],
                      rundown_md: str,
                      memory_dir: Path | None = None) -> dict[str, Path]:
    """Persist this week's three artefacts. Returns a dict of {kind: path}
    so the caller can log or surface them in the GUI.

    Creates `memory_dir` if it doesn't exist. Overwrites existing files for
    the same week — a re-run on Friday after a correction must replace,
    not append.
    """
    memory_dir = memory_dir or DEFAULT_MEMORY_DIR
    memory_dir.mkdir(parents=True, exist_ok=True)
    label = week_label(week_d)

    digest_p = _digest_path(label, memory_dir)
    digest_p.write_text(json.dumps(digest, indent=2), encoding="utf-8")

    headlines_p = _headlines_path(label, memory_dir)
    headline_lines = [f"- {h}" for h in headlines]
    headlines_p.write_text(
        f"# Headlines — {label}\n\n" + "\n".join(headline_lines) + "\n",
        encoding="utf-8",
    )

    rundown_p = _rundown_path(label, memory_dir)
    rundown_p.write_text(rundown_md, encoding="utf-8")

    return {"digest": digest_p, "headlines": headlines_p, "rundown": rundown_p}
