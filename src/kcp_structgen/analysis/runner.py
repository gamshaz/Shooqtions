"""End-to-end orchestrator for the weekly rundown.

`generate_weekly_rundown(week_d)` is the single public entry point. It
wires every Layer 2 module together: loads CME daily digests, flow rows,
client trades, events, FOMC tone summaries, daily commentary, prior weeks
from memory; builds the structured digest; calls `claude -p` with the
weekly-analysis system prompt; saves the rundown + digest + headlines.

Never raises in normal operation. Anything that fails (missing file,
network error, LLM timeout, parse failure) becomes a warning string on
the returned `RunResult`. The rest of the pipeline keeps going with what
it has.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .aggregator import build_digest
from .classifier import classify_events
from .cme_loader import load_cme_voi
from .commentary_loader import cleanup_old_raw, load_week_commentary
from .event_matcher import dedupe_fomc, tag_events
from .events_api import load_events_for_week, week_label, week_window
from .flow_loader import FlowLoaderError, load_client_trades, load_flow
from .fomc_scraper import attach_tone_summary
from .memory import extract_headlines, load_prior_weeks, save_week_outputs
from .segmenter import segment_week

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"

CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT_S = 180  # weekly analysis is a larger call than Layer 1's parse

WEEKLY_PROMPT_PATH = (
    Path(__file__).resolve().parent / "prompts" / "weekly_analysis.md"
)

COMMENTARY_KEEP_WEEKS = 1   # per user spec; configurable here only


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    week_label: str
    success: bool                    = False
    rundown_md: str                  = ""
    digest: dict                     = field(default_factory=dict)
    saved_paths: dict[str, Path]     = field(default_factory=dict)
    warnings: list[str]              = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers — one per pipeline step
# ---------------------------------------------------------------------------

def _trading_days(week_d: date) -> list[date]:
    monday, _ = week_window(week_d)
    return [monday + timedelta(days=i) for i in range(5)]


def _load_cme_for_week(week_d: date, data_root: Path,
                       warnings: list[str]) -> dict[str, dict]:
    """Read whichever CME daily digests are present for Mon-Fri."""
    out: dict[str, dict] = {}
    daily_dir = data_root / "oi" / "daily"
    for d in _trading_days(week_d):
        # Prefer pre-parsed .json; fall back to parsing .xls on the fly.
        json_path = daily_dir / f"{d.isoformat()}.json"
        if json_path.is_file():
            try:
                out[d.isoformat()] = json.loads(json_path.read_text(encoding="utf-8"))
                continue
            except json.JSONDecodeError:
                warnings.append(f"corrupt CME digest at {json_path.name}; will retry .xls")
        xls_path = daily_dir / f"{d.isoformat()}.xls"
        if xls_path.is_file():
            try:
                digest = load_cme_voi(xls_path, trade_date=d)
                # Persist parsed digest next to .xls so the next run is fast.
                json_path.write_text(json.dumps(digest, indent=2), encoding="utf-8")
                out[d.isoformat()] = digest
            except Exception as exc:
                warnings.append(
                    f"failed to parse {xls_path.name}: {type(exc).__name__}: {exc}"
                )
        else:
            warnings.append(f"CME file missing for {d.isoformat()}")
    return out


def _load_flow(data_root: Path, warnings: list[str]) -> list[dict]:
    path = data_root / "flow" / "flow.xlsx"
    if not path.is_file():
        warnings.append(f"flow sheet not found at {path}")
        return []
    try:
        return load_flow(path)
    except FlowLoaderError as exc:
        warnings.append(f"flow sheet unreadable: {exc}")
        return []


def _load_client_trades(data_root: Path, warnings: list[str]) -> list[dict]:
    path = data_root / "client_trades" / "client_trades.xlsx"
    if not path.is_file():
        warnings.append(f"client trades sheet not found at {path}")
        return []
    try:
        return load_client_trades(path)
    except FlowLoaderError as exc:
        warnings.append(f"client trades sheet unreadable: {exc}")
        return []


def _load_and_enrich_events(week_d: date, data_root: Path,
                            warnings: list[str],
                            allow_network: bool) -> list[dict]:
    """events API → tag → dedupe FOMC → classify → attach FOMC tone."""
    events_dir = data_root / "events"
    fomc_cache_dir = data_root / "fomc_statements"
    try:
        events = load_events_for_week(week_d, cache_dir=events_dir)
    except Exception as exc:
        warnings.append(f"events fetch failed: {type(exc).__name__}: {exc}")
        return []

    if not events:
        warnings.append("no events loaded for week (FMP unavailable / no cache)")
        return []

    tag_events(events)
    events = dedupe_fomc(events)
    classify_events(events)

    # Attach FOMC tone for any FOMC-tagged events in the week.
    for ev in events:
        if ev.get("matcher") == "FOMC":
            try:
                attach_tone_summary(
                    ev,
                    cache_dir=fomc_cache_dir,
                    allow_network=allow_network,
                )
                if ev.get("fomc_tone_summary") is None:
                    warnings.append(f"FOMC tone unavailable for {ev.get('date')}")
            except Exception as exc:
                warnings.append(
                    f"FOMC scraper crashed for {ev.get('date')}: "
                    f"{type(exc).__name__}: {exc}"
                )

    return events


def _load_commentary(week_d: date, data_root: Path,
                     warnings: list[str],
                     allow_network: bool) -> dict[str, dict]:
    base = data_root / "commentary"
    try:
        out = load_week_commentary(week_d,
                                   base_dir=base,
                                   allow_llm=allow_network)
    except Exception as exc:
        warnings.append(f"commentary loader crashed: {type(exc).__name__}: {exc}")
        return {}
    # Warn for trading days with no commentary entry.
    for d in _trading_days(week_d):
        if d.isoformat() not in out:
            warnings.append(f"no commentary for {d.isoformat()}")
    return out


def _load_prior_weeks(week_d: date, data_root: Path,
                      warnings: list[str]) -> list[dict]:
    memory_dir = data_root / "weekly_digests"
    try:
        return load_prior_weeks(week_d, n_weeks=3, memory_dir=memory_dir)
    except Exception as exc:
        warnings.append(f"prior-weeks load failed: {type(exc).__name__}: {exc}")
        return []


# ---------------------------------------------------------------------------
# claude -p main call
# ---------------------------------------------------------------------------

def _read_weekly_prompt() -> str | None:
    if not WEEKLY_PROMPT_PATH.is_file():
        return None
    return WEEKLY_PROMPT_PATH.read_text(encoding="utf-8")


def _strip_fences(text: str) -> str:
    """Defensive: model occasionally wraps markdown in ```...```."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    return s.strip()


def _call_weekly_analysis(digest: dict,
                          warnings: list[str]) -> str:
    """Pass the digest as JSON-string stdin to `claude -p` with the
    weekly-analysis system prompt. Returns markdown rundown, or "" on
    any failure (with a warning appended)."""
    if shutil.which(CLAUDE_CLI) is None:
        warnings.append(f"`{CLAUDE_CLI}` CLI not on PATH; cannot generate rundown")
        return ""

    system_prompt = _read_weekly_prompt()
    if system_prompt is None:
        warnings.append(f"weekly-analysis prompt missing at {WEEKLY_PROMPT_PATH}")
        return ""

    user_input = json.dumps(digest, indent=2, ensure_ascii=False)

    try:
        result = subprocess.run(
            [CLAUDE_CLI, "-p", "--append-system-prompt", system_prompt],
            input=user_input,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_S,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        warnings.append(f"claude -p timed out after {CLAUDE_TIMEOUT_S}s")
        return ""
    except FileNotFoundError as exc:
        warnings.append(f"claude -p exec failed: {exc}")
        return ""

    if result.returncode != 0:
        warnings.append(
            f"claude -p exited {result.returncode}. stderr: "
            f"{(result.stderr or '').strip()[:500]}"
        )
        return ""

    md = _strip_fences(result.stdout or "")
    if not md:
        warnings.append("claude -p returned empty markdown")
    return md


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def generate_weekly_rundown(week_d: date,
                            *,
                            data_root: Path | None = None,
                            allow_network: bool = True) -> RunResult:
    """End-to-end weekly rundown generation.

    Steps: load all inputs (CME, flow, client trades, events + FOMC tone,
    commentary, prior weeks) → build digest → call `claude -p` with the
    weekly-analysis prompt → save outputs to memory → cleanup old
    commentary raw files.

    Never raises. Anything that fails becomes a warning on the result;
    pipeline continues with what it has. If the LLM call itself fails,
    the digest is still saved so the user can inspect it.
    """
    data_root = data_root or DEFAULT_DATA_ROOT
    result = RunResult(week_label=week_label(week_d))

    daily_oi      = _load_cme_for_week(week_d, data_root, result.warnings)
    flow_rows     = _load_flow(data_root, result.warnings)
    client_rows   = _load_client_trades(data_root, result.warnings)
    events        = _load_and_enrich_events(week_d, data_root,
                                            result.warnings,
                                            allow_network=allow_network)
    commentary    = _load_commentary(week_d, data_root,
                                     result.warnings,
                                     allow_network=allow_network)
    prior_weeks   = _load_prior_weeks(week_d, data_root, result.warnings)
    segments      = segment_week(week_d, events)

    digest = build_digest(
        week_d,
        daily_oi_digests=daily_oi,
        flow_rows=flow_rows,
        client_rows=client_rows,
        events=events,
        segments=segments,
        prior_weeks=prior_weeks,
        warnings=list(result.warnings),
        daily_commentary=commentary,
    )
    result.digest = digest

    if allow_network:
        rundown_md = _call_weekly_analysis(digest, result.warnings)
    else:
        result.warnings.append("offline mode: skipped main weekly-analysis LLM call")
        rundown_md = ""

    result.rundown_md = rundown_md
    headlines = extract_headlines(rundown_md) if rundown_md else []

    # Persist whatever we have. Even if the LLM failed, save the digest so
    # the user can inspect what got built.
    try:
        memory_dir = data_root / "weekly_digests"
        result.saved_paths = save_week_outputs(
            week_d, digest, headlines, rundown_md or "",
            memory_dir=memory_dir,
        )
    except Exception as exc:
        result.warnings.append(
            f"failed to save week outputs: {type(exc).__name__}: {exc}"
        )

    # Cleanup old commentary raw files (cache kept forever). `today` is
    # pinned to the run's `week_d` so cleanup is deterministic relative to
    # the week being processed — useful when reprocessing past weeks.
    try:
        cleanup_old_raw(
            base_dir=data_root / "commentary",
            keep_weeks=COMMENTARY_KEEP_WEEKS,
            today=week_d,
        )
    except Exception as exc:
        result.warnings.append(
            f"commentary cleanup failed: {type(exc).__name__}: {exc}"
        )

    result.success = bool(rundown_md)
    return result
