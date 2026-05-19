"""Tests for commentary_loader.

Real DOCX files are written via python-docx into tmp_path. claude -p is
mocked everywhere — no network, no real LLM call.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from docx import Document

from kcp_structgen.analysis.commentary_loader import (
    _collect_day_text,
    _parse_two_pass_output,
    _read_docx,
    cleanup_old_raw,
    load_week_commentary,
)

# Reference week: ISO 2026-W47 (Mon 16 - Fri 20 Nov 2026)
THIS_WEEK = date(2026, 11, 18)
MONDAY    = date(2026, 11, 16)
TUESDAY   = date(2026, 11, 17)
WEDNESDAY = date(2026, 11, 18)


# ---------------------------------------------------------------------------
# Helpers — build real DOCX files on disk
# ---------------------------------------------------------------------------

def _write_docx(path: Path, paragraphs: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    for p in paragraphs:
        doc.add_paragraph(p)
    doc.save(str(path))


def _seed_day_raw(base_dir: Path, d: date,
                  itc_paragraphs: list[str] | None = None,
                  mni_paragraphs: list[str] | None = None,
                  itc_filename: str = "itc_us_morning.docx",
                  mni_filename: str = "mni_european_open.docx") -> None:
    folder = base_dir / "raw" / d.isoformat()
    if itc_paragraphs is not None:
        _write_docx(folder / itc_filename, itc_paragraphs)
    if mni_paragraphs is not None:
        _write_docx(folder / mni_filename, mni_paragraphs)


def _seed_cache(base_dir: Path, d: date, payload: dict) -> None:
    cache = base_dir / "cache" / f"{d.isoformat()}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload), encoding="utf-8")


def _mock_run(stdout: str, returncode: int = 0):
    class _R:
        def __init__(self, out, rc):
            self.stdout = out
            self.stderr = ""
            self.returncode = rc
    return _R(stdout, returncode)


# A realistic-shaped claude -p response we'll reuse across tests.
SAMPLE_OUTPUT = """\
=== HEADLINES ===
- ITC: Bessent reiterated tariff position on Sunday talk shows
- ITC: Fed's Bostic — "data continues to support patient approach"
- MNI: BoJ minutes signal slower normalisation pace
- MNI: Eurozone HICP final print in line at 2.4% YoY

=== COMMENTARY ===
Front-end was firm into the close on a quiet US session, with paper digesting
weekend tariff headlines that had little immediate read-through. European
trade was dominated by the BoJ minutes; nothing material from EU data.
"""


# ---------------------------------------------------------------------------
# _read_docx
# ---------------------------------------------------------------------------

def test_read_docx_extracts_paragraphs(tmp_path):
    p = tmp_path / "x.docx"
    _write_docx(p, ["First paragraph.", "Second paragraph.", "Third."])
    text = _read_docx(p)
    assert text == "First paragraph.\nSecond paragraph.\nThird."


def test_read_docx_drops_blank_paragraphs(tmp_path):
    p = tmp_path / "x.docx"
    _write_docx(p, ["A", "", "  ", "B"])
    text = _read_docx(p)
    assert text == "A\nB"


def test_read_docx_corrupt_returns_none(tmp_path):
    p = tmp_path / "broken.docx"
    p.write_bytes(b"not a real docx")
    with pytest.warns(UserWarning, match="failed to read"):
        result = _read_docx(p)
    assert result is None


# ---------------------------------------------------------------------------
# _collect_day_text
# ---------------------------------------------------------------------------

def test_collect_day_text_both_sources(tmp_path):
    _seed_day_raw(tmp_path, MONDAY,
                  itc_paragraphs=["ITC content"],
                  mni_paragraphs=["MNI content"])
    found = _collect_day_text(tmp_path / "raw" / MONDAY.isoformat())
    labels = [t[0] for t in found]
    texts = [t[2] for t in found]
    assert labels == ["ITC US Morning", "MNI European Open"]
    assert texts == ["ITC content", "MNI content"]


def test_collect_day_text_only_itc(tmp_path):
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["only ITC"])
    found = _collect_day_text(tmp_path / "raw" / MONDAY.isoformat())
    assert len(found) == 1
    assert found[0][0] == "ITC US Morning"


def test_collect_day_text_only_mni(tmp_path):
    _seed_day_raw(tmp_path, MONDAY, mni_paragraphs=["only MNI"])
    found = _collect_day_text(tmp_path / "raw" / MONDAY.isoformat())
    assert len(found) == 1
    assert found[0][0] == "MNI European Open"


def test_collect_day_text_neither_returns_empty(tmp_path):
    (tmp_path / "raw" / MONDAY.isoformat()).mkdir(parents=True)
    assert _collect_day_text(tmp_path / "raw" / MONDAY.isoformat()) == []


def test_collect_day_text_no_folder(tmp_path):
    assert _collect_day_text(tmp_path / "raw" / "1999-01-01") == []


def test_collect_day_text_case_insensitive_filenames(tmp_path):
    _seed_day_raw(tmp_path, MONDAY,
                  itc_paragraphs=["x"],
                  mni_paragraphs=["y"],
                  itc_filename="ITC_US.docx",
                  mni_filename="MNI_open.docx")
    found = _collect_day_text(tmp_path / "raw" / MONDAY.isoformat())
    assert {t[0] for t in found} == {"ITC US Morning", "MNI European Open"}


def test_collect_day_text_ignores_non_docx(tmp_path):
    folder = tmp_path / "raw" / MONDAY.isoformat()
    folder.mkdir(parents=True)
    (folder / "itc_us_morning.pdf").write_text("pdf placeholder")
    (folder / "notes.txt").write_text("random")
    assert _collect_day_text(folder) == []


def test_collect_day_text_skips_corrupt_file(tmp_path):
    folder = tmp_path / "raw" / MONDAY.isoformat()
    folder.mkdir(parents=True)
    # Corrupt ITC, valid MNI
    (folder / "itc_broken.docx").write_bytes(b"not a docx")
    _write_docx(folder / "mni_open.docx", ["MNI content survives"])
    with pytest.warns(UserWarning, match="failed to read"):
        found = _collect_day_text(folder)
    assert len(found) == 1
    assert found[0][0] == "MNI European Open"


# ---------------------------------------------------------------------------
# _parse_two_pass_output
# ---------------------------------------------------------------------------

def test_parse_two_pass_output_full():
    parsed = _parse_two_pass_output(SAMPLE_OUTPUT)
    assert len(parsed["headlines"]) == 4
    assert parsed["headlines"][0].startswith("ITC: Bessent")
    assert parsed["headlines"][2].startswith("MNI: BoJ")
    assert "Front-end was firm" in parsed["commentary"]


def test_parse_two_pass_output_strips_fences():
    raw = f"```\n{SAMPLE_OUTPUT}\n```"
    parsed = _parse_two_pass_output(raw)
    assert len(parsed["headlines"]) == 4


def test_parse_two_pass_output_only_headlines_marker():
    raw = "=== HEADLINES ===\n- ITC: one\n- ITC: two\n"
    parsed = _parse_two_pass_output(raw)
    assert parsed["headlines"] == ["ITC: one", "ITC: two"]
    assert parsed["commentary"] == ""


def test_parse_two_pass_output_only_commentary_marker():
    raw = "=== COMMENTARY ===\nQuiet session.\n"
    parsed = _parse_two_pass_output(raw)
    assert parsed["headlines"] == []
    assert parsed["commentary"] == "Quiet session."


def test_parse_two_pass_output_no_markers_returns_none():
    assert _parse_two_pass_output("just some text with no markers") is None


def test_parse_two_pass_output_empty_returns_none():
    assert _parse_two_pass_output("") is None
    assert _parse_two_pass_output("   \n  ") is None


def test_parse_two_pass_output_accepts_asterisk_bullets():
    raw = "=== HEADLINES ===\n* ITC: one\n* MNI: two\n=== COMMENTARY ===\nx."
    parsed = _parse_two_pass_output(raw)
    assert parsed["headlines"] == ["ITC: one", "MNI: two"]


# ---------------------------------------------------------------------------
# load_week_commentary — cache + LLM paths
# ---------------------------------------------------------------------------

def test_load_week_cache_hit_no_llm_call(tmp_path):
    _seed_cache(tmp_path, MONDAY, {
        "date": MONDAY.isoformat(),
        "sources": ["itc_us_morning.docx"],
        "headlines": ["ITC: cached headline"],
        "commentary": "cached prose",
    })
    with patch("subprocess.run") as mock_run:
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
        mock_run.assert_not_called()
    assert MONDAY.isoformat() in out
    assert out[MONDAY.isoformat()]["headlines"] == ["ITC: cached headline"]


def test_load_week_writes_cache_on_llm_path(tmp_path):
    _seed_day_raw(tmp_path, MONDAY,
                  itc_paragraphs=["ITC content"],
                  mni_paragraphs=["MNI content"])
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run(SAMPLE_OUTPUT)):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert MONDAY.isoformat() in out
    entry = out[MONDAY.isoformat()]
    assert len(entry["headlines"]) == 4
    assert "Front-end was firm" in entry["commentary"]
    # Cache file written
    cache = tmp_path / "cache" / f"{MONDAY.isoformat()}.json"
    assert cache.is_file()
    cached = json.loads(cache.read_text(encoding="utf-8"))
    assert cached["headlines"] == entry["headlines"]


def test_load_week_missing_day_silently_skipped(tmp_path):
    """No raw folder, no cache → day simply absent from result."""
    _seed_day_raw(tmp_path, MONDAY,
                  itc_paragraphs=["only Monday has data"])
    # Tuesday has nothing
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run(SAMPLE_OUTPUT)):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert MONDAY.isoformat() in out
    assert TUESDAY.isoformat() not in out


def test_load_week_allow_llm_false_skips_uncached_days(tmp_path):
    """allow_llm=False with raw files but no cache → that day is absent."""
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["raw exists"])
    with patch("subprocess.run") as mock_run:
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path,
                                   allow_llm=False)
        mock_run.assert_not_called()
    assert out == {}


def test_load_week_llm_failure_no_cache_written(tmp_path):
    """If claude -p exits non-zero, the day is skipped and no cache written."""
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["x"])
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run("error", returncode=1)):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert out == {}
    assert not (tmp_path / "cache" / f"{MONDAY.isoformat()}.json").exists()


def test_load_week_llm_timeout_skips_day(tmp_path):
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["x"])

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["claude"], timeout=120)

    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", side_effect=_timeout):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert out == {}


def test_load_week_unparseable_response_skipped(tmp_path):
    """LLM returns text with no === markers → day skipped, no cache."""
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["x"])
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run("just some words")):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert out == {}


def test_load_week_cli_missing_skips_with_no_cache(tmp_path):
    """If `claude` CLI is not on PATH and no cache exists, day is skipped."""
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["x"])
    with patch("shutil.which", return_value=None):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert out == {}


def test_load_week_corrupt_cache_treated_as_miss(tmp_path):
    """Corrupt cache file is skipped (falls through to LLM path)."""
    cache = tmp_path / "cache" / f"{MONDAY.isoformat()}.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("garbage{{", encoding="utf-8")
    _seed_day_raw(tmp_path, MONDAY, itc_paragraphs=["raw"])
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run(SAMPLE_OUTPUT)):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    assert MONDAY.isoformat() in out


def test_load_week_sources_filenames_recorded(tmp_path):
    _seed_day_raw(tmp_path, MONDAY,
                  itc_paragraphs=["a"], mni_paragraphs=["b"])
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("subprocess.run", return_value=_mock_run(SAMPLE_OUTPUT)):
        out = load_week_commentary(THIS_WEEK, base_dir=tmp_path)
    sources = out[MONDAY.isoformat()]["sources"]
    assert "itc_us_morning.docx" in sources
    assert "mni_european_open.docx" in sources


# ---------------------------------------------------------------------------
# cleanup_old_raw
# ---------------------------------------------------------------------------

def test_cleanup_old_raw_keeps_recent(tmp_path):
    today = date(2026, 11, 18)
    fresh_d = today - timedelta(days=3)
    _seed_day_raw(tmp_path, fresh_d, itc_paragraphs=["x"])
    removed = cleanup_old_raw(base_dir=tmp_path, keep_weeks=1, today=today)
    assert removed == 0
    assert (tmp_path / "raw" / fresh_d.isoformat()).is_dir()


def test_cleanup_old_raw_deletes_old(tmp_path):
    today = date(2026, 11, 18)
    old_d = today - timedelta(weeks=3)
    _seed_day_raw(tmp_path, old_d, itc_paragraphs=["x"])
    removed = cleanup_old_raw(base_dir=tmp_path, keep_weeks=1, today=today)
    assert removed == 1
    assert not (tmp_path / "raw" / old_d.isoformat()).exists()


def test_cleanup_old_raw_never_touches_cache(tmp_path):
    today = date(2026, 11, 18)
    old_d = today - timedelta(weeks=4)
    _seed_day_raw(tmp_path, old_d, itc_paragraphs=["x"])
    _seed_cache(tmp_path, old_d, {"date": old_d.isoformat()})
    cleanup_old_raw(base_dir=tmp_path, keep_weeks=1, today=today)
    cache_file = tmp_path / "cache" / f"{old_d.isoformat()}.json"
    assert cache_file.is_file()


def test_cleanup_old_raw_ignores_non_iso_folder_names(tmp_path):
    raw_root = tmp_path / "raw"
    raw_root.mkdir(parents=True)
    (raw_root / "not-a-date").mkdir()
    (raw_root / "garbage").mkdir()
    removed = cleanup_old_raw(base_dir=tmp_path, keep_weeks=1)
    assert removed == 0
    assert (raw_root / "not-a-date").is_dir()  # untouched


def test_cleanup_old_raw_no_raw_dir_returns_zero(tmp_path):
    assert cleanup_old_raw(base_dir=tmp_path) == 0
