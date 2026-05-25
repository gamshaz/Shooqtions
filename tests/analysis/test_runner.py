"""Integration tests for runner.py.

Every external boundary is mocked: claude -p (twice — for commentary and
for weekly analysis), urllib (for FMP and Fed). CME data is fed via the
.json digest path (skipping the .xls parse) for speed.

The runner is wired to never raise; tests assert it warns instead.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path
from unittest.mock import patch

import openpyxl
import pytest
from docx import Document

from kcp_structgen.analysis.runner import (
    RunResult,
    generate_weekly_rundown,
)

# Reference week: 2026-W47, Wed = 2026-11-18
THIS_WEEK = date(2026, 11, 18)
MONDAY    = date(2026, 11, 16)
TUESDAY   = date(2026, 11, 17)
WEDNESDAY = date(2026, 11, 18)
THURSDAY  = date(2026, 11, 19)
FRIDAY    = date(2026, 11, 20)
WEEK_DAYS = [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mk_oi_digest(d: date,
                  z6_call_oi: int = 50000,
                  z6_call_delta: int = 0) -> dict:
    """Minimal CME digest with one strike of activity."""
    return {
        "trade_date": d.isoformat(),
        "futures": {
            "Z6": {"oi": 1_000_000, "oi_change": 0, "volume": 100_000},
        },
        "options": {
            "SR3": {
                "Z6": {
                    "calls": [{
                        "strike": 96.75, "volume": 1000,
                        "oi": z6_call_oi, "oi_change": z6_call_delta,
                    }],
                    "puts": [],
                }
            }
        },
    }


def _write_oi_digests(data_root: Path) -> None:
    daily_dir = data_root / "oi" / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    # Build a simple OI-build story across the week
    deltas = [3000, 5000, -5000, -2000, 0]
    for d, delta in zip(WEEK_DAYS, deltas):
        digest = _mk_oi_digest(d, z6_call_delta=delta)
        (daily_dir / f"{d.isoformat()}.json").write_text(
            json.dumps(digest), encoding="utf-8")


def _write_flow_xlsx(data_root: Path) -> None:
    folder = data_root / "flow"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "flow.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["date", "raw_note", "product", "expiry",
               "structure", "size", "direction", "price"])
    ws.append([TUESDAY.isoformat(), "SFRZ6 96.75 c bid",
               "SR3", "Z6", "96.75 c", 5000, "buy", 2.0])
    wb.save(path)


def _write_client_trades_xlsx(data_root: Path) -> None:
    folder = data_root / "client_trades"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "client_trades.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["date", "raw_note", "product", "expiry",
               "structure", "size", "direction", "price"])
    ws.append([WEDNESDAY.isoformat(), "KCP: sold Z6 96.75 c 2k for client X",
               "SR3", "Z6", "96.75 c", 2000, "sell", 1.5])
    wb.save(path)


def _write_commentary_docx(data_root: Path, day: date) -> None:
    folder = data_root / "commentary" / "raw" / day.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    for fname, body in (
        ("itc_us_morning.docx", "ITC content for the day."),
        ("mni_european_open.docx", "MNI content for the day."),
    ):
        doc = Document()
        doc.add_paragraph(body)
        doc.save(str(folder / fname))


def _seed_events_cache(data_root: Path, label: str) -> None:
    folder = data_root / "events"
    folder.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "date":       WEDNESDAY.isoformat(),
            "country":    "US",
            "event_name": "Consumer Price Index YoY",
            "previous":   2.7,
            "estimate":   2.9,
            "actual":     3.2,
            "impact":     "High",
            "matcher":    None,  # tag_events will fill
        }
    ]
    (folder / f"{label}.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_commentary_cache(data_root: Path, day: date) -> None:
    folder = data_root / "commentary" / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "date":       day.isoformat(),
        "sources":    ["itc_us_morning.docx"],
        "headlines":  ["ITC: a headline for the day"],
        "commentary": "Brief commentary.",
    }
    (folder / f"{day.isoformat()}.json").write_text(json.dumps(payload), encoding="utf-8")


def _mock_subprocess_run(stdout: str, returncode: int = 0):
    class _R:
        def __init__(self, out, rc):
            self.stdout = out
            self.stderr = ""
            self.returncode = rc
    return _R(stdout, returncode)


# ---------------------------------------------------------------------------
# Empty data root
# ---------------------------------------------------------------------------

def test_empty_data_root_no_crash(tmp_path):
    """Nothing exists anywhere. Runner must succeed with all warnings."""
    with patch("shutil.which", return_value=None):
        # Disallow network so we don't accidentally hit FMP / claude
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=False,
        )
    assert isinstance(result, RunResult)
    assert result.week_label == "2026-W47"
    assert result.success is False
    assert result.rundown_md == ""
    # Five CME-missing warnings, flow/client missing, events unavailable, etc.
    assert any("CME file missing" in w for w in result.warnings)
    assert any("flow sheet not found" in w for w in result.warnings)
    assert any("client trades sheet not found" in w for w in result.warnings)


def test_offline_mode_does_not_call_subprocess(tmp_path):
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run") as mock_run:
        generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=False,
        )
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

SAMPLE_RUNDOWN_MD = """\
## This week's headlines

- Paper built Z6 96.75 calls Mon-Tue then unwound them on hot CPI Wed
- Quiet flow rest of the week

## Events

CPI Wed (hot, 3.2 vs 2.9): paper covered Z6 96.75c into the close.

## OI themes

Mid-week unwind in Z6 calls.

## Flow highlights

- Quiet outside Wednesday's reaction

## KCP client activity

KCP sold 2k Z6 96.75c on Wednesday.

## Watch for next week

- Watch for follow-through on the Z6 unwind
"""


def test_end_to_end_happy_path(tmp_path):
    """Full pipeline with all inputs present and mocked LLM responses."""
    _write_oi_digests(tmp_path)
    _write_flow_xlsx(tmp_path)
    _write_client_trades_xlsx(tmp_path)
    _seed_events_cache(tmp_path, "2026-W47")
    # Pre-seed commentary cache to skip the inner claude -p call for commentary
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    # Mock the weekly-analysis claude -p call only
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run",
               return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path,
            allow_network=True,
        )

    assert result.success is True
    assert "## This week's headlines" in result.rundown_md
    assert "## Watch for next week" in result.rundown_md

    # Digest carries through
    assert result.digest["week"] == "2026-W47"
    assert result.digest["events"][0]["matcher"] == "CPI"
    assert result.digest["events"][0]["surprise"] == "hot"

    # Saved to disk
    assert "digest" in result.saved_paths
    assert result.saved_paths["digest"].is_file()
    assert result.saved_paths["rundown"].is_file()

    # Headlines extracted
    headlines_path = result.saved_paths["headlines"]
    headlines_content = headlines_path.read_text(encoding="utf-8")
    assert "Paper built Z6 96.75 calls" in headlines_content


def test_segments_built_around_event(tmp_path):
    """When events are present, the digest carries the segmented week."""
    _write_oi_digests(tmp_path)
    _write_flow_xlsx(tmp_path)
    _write_client_trades_xlsx(tmp_path)
    _seed_events_cache(tmp_path, "2026-W47")
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run",
               return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    names = [s["name"] for s in result.digest["segments"]]
    assert names == ["pre_CPI", "event_day_CPI", "post_CPI"]


def test_daily_commentary_attached_to_segments(tmp_path):
    _write_oi_digests(tmp_path)
    _seed_events_cache(tmp_path, "2026-W47")
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run",
               return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    # pre_CPI = Tue only
    pre = result.digest["segments"][0]
    assert list(pre["daily_commentary"].keys()) == [TUESDAY.isoformat()]
    assert pre["daily_commentary"][TUESDAY.isoformat()]["headlines"] \
        == ["ITC: a headline for the day"]


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_llm_failure_still_saves_digest(tmp_path):
    """If claude -p exits non-zero, success=False but digest persists."""
    _write_oi_digests(tmp_path)
    _seed_events_cache(tmp_path, "2026-W47")
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run",
               return_value=_mock_subprocess_run("err", returncode=1)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    assert result.success is False
    assert result.rundown_md == ""
    assert any("exited 1" in w for w in result.warnings)
    # Digest still saved
    assert result.saved_paths["digest"].is_file()
    saved = json.loads(result.saved_paths["digest"].read_text(encoding="utf-8"))
    assert saved["week"] == "2026-W47"


def test_llm_timeout_captured_as_warning(tmp_path):
    _write_oi_digests(tmp_path)
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=180)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", side_effect=_timeout):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    assert result.success is False
    assert any("timed out" in w for w in result.warnings)


def test_claude_cli_missing(tmp_path):
    _write_oi_digests(tmp_path)
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value=None):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    assert result.success is False
    assert any("CLI not on PATH" in w for w in result.warnings)


def test_warnings_for_missing_cme_days(tmp_path):
    """Only Mon + Wed have CME data; other days warn."""
    daily_dir = tmp_path / "oi" / "daily"
    daily_dir.mkdir(parents=True)
    for d in (MONDAY, WEDNESDAY):
        (daily_dir / f"{d.isoformat()}.json").write_text(
            json.dumps(_mk_oi_digest(d)), encoding="utf-8")
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    for d in (TUESDAY, THURSDAY, FRIDAY):
        assert any(f"CME file missing for {d.isoformat()}" in w
                   for w in result.warnings)


def test_corrupt_cme_json_falls_back_to_warning(tmp_path):
    daily_dir = tmp_path / "oi" / "daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / f"{MONDAY.isoformat()}.json").write_text("garbage{{",
                                                           encoding="utf-8")
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    assert any("corrupt CME digest" in w or "CME file missing" in w
               for w in result.warnings)


def test_no_events_yields_week_flat_segment(tmp_path):
    """No events file, runner succeeds; segmenter falls back to week_flat."""
    _write_oi_digests(tmp_path)
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)
    # No events cache file

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("urllib.request.urlopen") as mock_urlopen, \
         patch("subprocess.run",
               return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        # Make FMP fail too (no API key configured in tests)
        mock_urlopen.side_effect = Exception("no network in tests")
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    names = [s["name"] for s in result.digest["segments"]]
    assert names == ["week_flat"]


# ---------------------------------------------------------------------------
# Prior weeks loaded
# ---------------------------------------------------------------------------

def test_prior_weeks_loaded_into_digest(tmp_path):
    # Seed a prior week (W46) digest
    memory_dir = tmp_path / "weekly_digests"
    memory_dir.mkdir(parents=True)
    (memory_dir / "2026-W46.json").write_text(
        json.dumps({"week": "2026-W46", "events": []}), encoding="utf-8")
    (memory_dir / "2026-W46_headlines.md").write_text(
        "# Headlines — 2026-W46\n\n- prior week theme\n", encoding="utf-8")

    _write_oi_digests(tmp_path)
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        result = generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    assert len(result.digest["prior_weeks"]) == 1
    assert result.digest["prior_weeks"][0]["week"] == "2026-W46"
    assert result.digest["prior_weeks"][0]["headlines"] == ["prior week theme"]


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def test_old_commentary_raw_cleaned_up(tmp_path):
    """After a run, raw DOCX folders older than 1 week are deleted; cache
    files survive forever."""
    # Seed an OLD raw folder (4 weeks ago) and its cache
    old_d = date(2026, 10, 19)
    _write_commentary_docx(tmp_path, old_d)
    # Also seed a fresh week's commentary cache so the run finds something
    for d in WEEK_DAYS:
        _seed_commentary_cache(tmp_path, d)
    _write_oi_digests(tmp_path)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_subprocess_run(SAMPLE_RUNDOWN_MD)):
        generate_weekly_rundown(
            THIS_WEEK, data_root=tmp_path, allow_network=True,
        )

    old_raw_folder = tmp_path / "commentary" / "raw" / old_d.isoformat()
    assert not old_raw_folder.exists(), "old raw folder should have been cleaned"
