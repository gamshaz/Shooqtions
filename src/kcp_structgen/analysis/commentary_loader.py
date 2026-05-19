"""Daily research-commentary loader.

For each trading day Mon-Fri in a given ISO week, read the desk's saved
ITC and MNI reports (Word docs in `data/commentary/raw/<date>/`), then
ask `claude -p` to produce a two-pass output:

  HEADLINES   — every named event/data print/speaker/policy line/move,
                verbatim from the reports, labelled with source.
  COMMENTARY  — 3-5 sentence narrative of what moved markets that day.

Results are cached per-day at `data/commentary/cache/<date>.json` so the
LLM is called at most once per day. Raw DOCX files older than
`keep_weeks` are deleted by `cleanup_old_raw()` — caches kept forever.

DOCX is the default format (desk copy-pastes from email into Word). PDF
support is deferred to v2.1 (tracked in v2_backlog).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import warnings
from datetime import date, timedelta
from pathlib import Path

from docx import Document

from .events_api import week_window

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_BASE_DIR = REPO_ROOT / "data" / "commentary"

CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT_S = 120

# Filename matchers (case-insensitive). Loose on purpose so the desk doesn't
# have to rename precisely — `itc.docx`, `itc_morning.docx`, `ITC US.docx`
# all match.
_ITC_RE = re.compile(r"^itc.*\.docx$", re.IGNORECASE)
_MNI_RE = re.compile(r"^mni.*\.docx$", re.IGNORECASE)

# Markers the LLM is told to emit (see SYSTEM_PROMPT).
_HEADLINES_MARKER  = "=== HEADLINES ==="
_COMMENTARY_MARKER = "=== COMMENTARY ==="

SYSTEM_PROMPT = """\
You are processing rates-desk research notes for one trading day. The user
message contains 1-2 notes (ITC US Morning and/or MNI European Open),
labelled. Read everything and produce exactly two sections in plain text.

=== HEADLINES ===
Every named event, data print, central bank speaker, policy line, and
material market move the notes mention. One per line, starting with
"- ". Keep verbatim phrasing where you can. Do NOT compress, summarise,
or skip headlines. If the notes list thirty items between them, you
list thirty. Skip ONLY:
  - non-rates content (equities, FX, EM credit, crypto, commodities
    that don't affect rates)
  - pure ticker recaps without commentary
  - boilerplate (disclaimers, contact lines, "research from ...")
Label each headline with its source: "ITC:" or "MNI:".

=== COMMENTARY ===
3 to 5 sentences capturing what moved markets that day and why,
synthesised across both notes. Plain prose, no bullets. Skip ticker
recaps; capture the narrative thread. If only one note is provided,
base the commentary on it alone.

Reply with both sections in that order. Use those exact === markers.
No preamble, no markdown headers, no fences.
"""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _raw_dir(d: date, base_dir: Path) -> Path:
    return base_dir / "raw" / d.isoformat()


def _cache_path(d: date, base_dir: Path) -> Path:
    return base_dir / "cache" / f"{d.isoformat()}.json"


# ---------------------------------------------------------------------------
# DOCX reading
# ---------------------------------------------------------------------------

def _read_docx(path: Path) -> str | None:
    """Return concatenated paragraph text from a DOCX file. None on failure
    (corrupt file, missing python-docx support, etc.)."""
    try:
        doc = Document(str(path))
    except Exception as exc:
        warnings.warn(f"failed to read {path.name}: {type(exc).__name__}: {exc}")
        return None
    lines = [p.text.strip() for p in doc.paragraphs]
    text = "\n".join(line for line in lines if line)
    return text or None


def _collect_day_text(date_folder: Path) -> list[tuple[str, str, str]]:
    """Find ITC + MNI DOCX files in a date folder.

    Returns a list of (label, filename, text) tuples. Each entry has been
    successfully read. Order: ITC first, MNI second (stable across runs).
    Empty list if no matching docs found or all reads failed.
    """
    if not date_folder.is_dir():
        return []

    found: list[tuple[str, str, str]] = []

    # ITC first
    for f in sorted(date_folder.iterdir()):
        if f.is_file() and _ITC_RE.match(f.name):
            text = _read_docx(f)
            if text is not None:
                found.append(("ITC US Morning", f.name, text))
                break   # one ITC report per day

    # Then MNI
    for f in sorted(date_folder.iterdir()):
        if f.is_file() and _MNI_RE.match(f.name):
            text = _read_docx(f)
            if text is not None:
                found.append(("MNI European Open", f.name, text))
                break   # one MNI report per day

    return found


# ---------------------------------------------------------------------------
# claude -p two-pass summariser
# ---------------------------------------------------------------------------

def _build_user_input(day_sources: list[tuple[str, str, str]]) -> str:
    """Compose the user-side text fed to claude -p."""
    blocks: list[str] = []
    for label, _filename, text in day_sources:
        blocks.append(f"--- {label} ---\n{text}\n")
    return "\n".join(blocks)


def _strip_fences(text: str) -> str:
    """Defensive: model occasionally wraps output in ```...``` despite the
    'no fences' instruction. Strip them belt-and-braces."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    return s.strip()


def _parse_two_pass_output(raw: str) -> dict | None:
    """Split the LLM reply into {headlines: [...], commentary: '...'}.

    Returns None if the markers can't be found at all. Returns a partial
    dict (with possibly empty lists / blank commentary) if only one marker
    is present — best-effort, not all-or-nothing.
    """
    text = _strip_fences(raw)
    if not text:
        return None

    has_h = _HEADLINES_MARKER in text
    has_c = _COMMENTARY_MARKER in text

    if not has_h and not has_c:
        return None

    headlines: list[str] = []
    commentary = ""

    if has_h and has_c:
        # Both markers present: take what's between them, and what's after
        # the commentary marker.
        h_idx = text.index(_HEADLINES_MARKER) + len(_HEADLINES_MARKER)
        c_idx = text.index(_COMMENTARY_MARKER)
        headlines_block = text[h_idx:c_idx].strip()
        commentary = text[c_idx + len(_COMMENTARY_MARKER):].strip()
    elif has_h:
        h_idx = text.index(_HEADLINES_MARKER) + len(_HEADLINES_MARKER)
        headlines_block = text[h_idx:].strip()
    else:
        c_idx = text.index(_COMMENTARY_MARKER)
        headlines_block = ""
        commentary = text[c_idx + len(_COMMENTARY_MARKER):].strip()

    # Parse headlines bullets (lines starting with "- ")
    for line in headlines_block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            headlines.append(stripped[2:].strip())

    return {"headlines": headlines, "commentary": commentary}


def _call_claude(user_input: str) -> str | None:
    """Run `claude -p` with our system prompt. Returns stdout on success,
    None on any failure (CLI missing, timeout, non-zero exit, empty
    stdout)."""
    if shutil.which(CLAUDE_CLI) is None:
        return None
    try:
        result = subprocess.run(
            [CLAUDE_CLI, "-p", "--append-system-prompt", SYSTEM_PROMPT],
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
    return (result.stdout or "").strip() or None


def _summarise_day(d: date,
                   day_sources: list[tuple[str, str, str]]) -> dict | None:
    """Build the user input, call claude -p, parse the response. Returns
    {date, sources, headlines, commentary} on success, None on failure."""
    if not day_sources:
        return None
    raw = _call_claude(_build_user_input(day_sources))
    if raw is None:
        return None
    parsed = _parse_two_pass_output(raw)
    if parsed is None:
        return None
    return {
        "date":       d.isoformat(),
        "sources":    [filename for _label, filename, _text in day_sources],
        "headlines":  parsed["headlines"],
        "commentary": parsed["commentary"],
    }


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _read_cache(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_week_commentary(week_d: date,
                         *,
                         base_dir: Path | None = None,
                         allow_llm: bool = True) -> dict[str, dict]:
    """Return per-day commentary entries for the ISO week containing
    `week_d`. Keys are ISO date strings; values are
    {date, sources, headlines, commentary}.

    Resolution order per trading day:
      1. cache/<date>.json hit → return it.
      2. raw/<date>/ has matching DOCX files → read, call claude -p, write
         cache, return.
      3. No raw files OR (allow_llm=False and no cache) → day absent from
         result (caller iterates only what's there; no padding).
    """
    base_dir = base_dir or DEFAULT_BASE_DIR
    monday, _friday = week_window(week_d)
    week_days = [monday + timedelta(days=i) for i in range(5)]

    out: dict[str, dict] = {}
    for d in week_days:
        cache = _read_cache(_cache_path(d, base_dir))
        if cache is not None:
            out[d.isoformat()] = cache
            continue
        if not allow_llm:
            continue
        sources = _collect_day_text(_raw_dir(d, base_dir))
        if not sources:
            continue
        summary = _summarise_day(d, sources)
        if summary is None:
            continue
        _write_cache(_cache_path(d, base_dir), summary)
        out[d.isoformat()] = summary
    return out


def cleanup_old_raw(base_dir: Path | None = None,
                    keep_weeks: int = 1,
                    *,
                    today: date | None = None) -> int:
    """Delete raw date-folders older than `keep_weeks` weeks from `today`.

    Cache JSON files are never touched. Returns the number of folders
    removed.
    """
    base_dir = base_dir or DEFAULT_BASE_DIR
    raw_root = base_dir / "raw"
    if not raw_root.is_dir():
        return 0

    threshold = (today or date.today()) - timedelta(weeks=keep_weeks)

    removed = 0
    for child in raw_root.iterdir():
        if not child.is_dir():
            continue
        try:
            folder_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if folder_date < threshold:
            shutil.rmtree(child)
            removed += 1
    return removed
