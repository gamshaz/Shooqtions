"""Tests for cme_loader.

Sample file: VoiDetailsForProduct.xls at repo root (committed). Known values
below were verified during planning by direct file inspection.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from kcp_structgen.analysis.cme_loader import (
    CMELoaderError,
    _expiry_code,
    _expiry_in_scope,
    _parse_int,
    _parse_strike,
    _quarterly_in_scope,
    _trade_date_from_filename,
    load_cme_voi,
    parse_and_save,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_XLS = REPO_ROOT / "VoiDetailsForProduct.xls"
SAMPLE_DATE = date(2026, 4, 22)  # placeholder; the file has no embedded date


# ---------------------------------------------------------------------------
# Pure-function tests (run without the sample file)
# ---------------------------------------------------------------------------

def test_parse_strike_basic():
    assert _parse_strike("9631") == 96.31
    assert _parse_strike("9700") == 97.00
    assert _parse_strike("10100") == 101.00


def test_parse_strike_bad_input_raises():
    with pytest.raises(ValueError):
        _parse_strike("abc")
    with pytest.raises(ValueError):
        _parse_strike("96.31")  # already decimal — not the CME format


def test_parse_int_thousands():
    assert _parse_int("1,735") == 1735
    assert _parse_int("-3,986") == -3986
    assert _parse_int("0") == 0
    assert _parse_int(0) == 0


def test_parse_int_nan_and_none():
    import math
    assert _parse_int(None) == 0
    assert _parse_int(float("nan")) == 0
    assert _parse_int("") == 0
    assert _parse_int("nan") == 0


def test_parse_int_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_int("abc")
    with pytest.raises(ValueError):
        _parse_int("12.34")  # decimal — not allowed in volume / OI fields


def test_expiry_code_all_months():
    assert _expiry_code("JAN", "26") == "F6"
    assert _expiry_code("FEB", "26") == "G6"
    assert _expiry_code("MAR", "26") == "H6"
    assert _expiry_code("APR", "26") == "J6"
    assert _expiry_code("MAY", "26") == "K6"
    assert _expiry_code("JUN", "26") == "M6"
    assert _expiry_code("JUL", "26") == "N6"
    assert _expiry_code("AUG", "26") == "Q6"
    assert _expiry_code("SEP", "26") == "U6"
    assert _expiry_code("OCT", "26") == "V6"
    assert _expiry_code("NOV", "26") == "X6"
    assert _expiry_code("DEC", "26") == "Z6"
    assert _expiry_code("DEC", "27") == "Z7"


def test_expiry_code_unknown_month_raises():
    with pytest.raises(ValueError):
        _expiry_code("FOO", "26")


def test_expiry_in_scope():
    # F/G/H, J/K/M, N/Q/U, V/X/Z for years 6 + 7
    assert _expiry_in_scope("K6")
    assert _expiry_in_scope("Z6")
    assert _expiry_in_scope("H7")
    # Out of year scope
    assert not _expiry_in_scope("Z5")
    assert not _expiry_in_scope("Z8")
    # Bad shape
    assert not _expiry_in_scope("ZZZ")
    assert not _expiry_in_scope("")


def test_quarterly_in_scope():
    assert _quarterly_in_scope("H6")
    assert _quarterly_in_scope("Z7")
    # Non-quarterly months
    assert not _quarterly_in_scope("K6")
    assert not _quarterly_in_scope("V6")
    # Out of year scope
    assert not _quarterly_in_scope("Z5")


def test_trade_date_from_filename():
    assert _trade_date_from_filename(Path("2026-04-22.xls")) == date(2026, 4, 22)
    assert _trade_date_from_filename(Path("oi/daily/2026-12-31.xls")) == date(2026, 12, 31)
    assert _trade_date_from_filename(Path("VoiDetailsForProduct.xls")) is None


# ---------------------------------------------------------------------------
# Integration tests against the committed sample file
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_digest():
    if not SAMPLE_XLS.is_file():
        pytest.skip(f"sample file not present: {SAMPLE_XLS}")
    return load_cme_voi(SAMPLE_XLS, trade_date=SAMPLE_DATE)


def test_top_level_shape(sample_digest):
    assert set(sample_digest.keys()) == {"trade_date", "futures", "options"}
    assert sample_digest["trade_date"] == "2026-04-22"


def test_futures_only_quarterlies(sample_digest):
    futures = sample_digest["futures"]
    # Every key must be a quarterly in scope
    for expiry in futures.keys():
        assert expiry[0] in "HMUZ", f"unexpected futures expiry {expiry!r}"
        assert expiry[1] in "67", f"out-of-scope futures year {expiry!r}"
    # Specific quarterlies expected present
    for expected in ("H6", "M6", "U6", "Z6", "H7", "M7", "U7", "Z7"):
        assert expected in futures, f"missing in-scope quarterly {expected}"
    # FEB / APR / MAY / OCT futures must NOT be present
    for forbidden in ("G6", "J6", "K6", "V6"):
        assert forbidden not in futures, f"non-quarterly leaked: {forbidden}"


def test_futures_sample_values(sample_digest):
    """Values verified during planning by raw-file inspection."""
    m6 = sample_digest["futures"]["M6"]
    assert m6["oi"] == 1_225_252
    assert m6["oi_change"] == -8_093
    assert m6["volume"] == 206_830

    z6 = sample_digest["futures"]["Z6"]
    assert z6["oi"] == 1_429_638
    assert z6["oi_change"] == -8_518


def test_options_products(sample_digest):
    assert set(sample_digest["options"].keys()) == {"SR3", "0Q"}


def test_sr3_expiries_in_scope(sample_digest):
    """All emitted SR3 expiries must be in §4.2 monthly scope."""
    for expiry in sample_digest["options"]["SR3"].keys():
        assert _expiry_in_scope(expiry), f"out-of-scope expiry leaked: {expiry}"


def test_q0_expiries_in_scope(sample_digest):
    for expiry in sample_digest["options"]["0Q"].keys():
        assert _expiry_in_scope(expiry), f"out-of-scope expiry leaked: {expiry}"


def test_sr3_k6_call_specific_strike(sample_digest):
    """SR3 K6 96.31 call must show oi=38499, oi_change=+400, volume=2735.
    Verified by raw-file inspection during planning."""
    k6_calls = sample_digest["options"]["SR3"]["K6"]["calls"]
    match = next((r for r in k6_calls if r["strike"] == 96.31), None)
    assert match is not None, "96.31 call not found in SR3 K6"
    assert match["oi"] == 38_499
    assert match["oi_change"] == 400
    assert match["volume"] == 2735


def test_sr3_k6_put_block_present(sample_digest):
    """K6 puts block exists and is non-empty (sanity)."""
    puts = sample_digest["options"]["SR3"]["K6"]["puts"]
    assert len(puts) > 0
    # Every entry has the expected shape
    for row in puts:
        assert set(row.keys()) == {"strike", "volume", "oi", "oi_change"}


def test_q0_has_at_least_one_expiry(sample_digest):
    assert len(sample_digest["options"]["0Q"]) > 0


def test_no_weeklies_or_midcurves_present(sample_digest):
    """Loader must filter to SR3 + 0Q only — no weeklies, no 2Y-5Y."""
    assert "2Y" not in sample_digest["options"]
    assert "3Y" not in sample_digest["options"]
    assert "weeklies" not in sample_digest["options"]


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

def test_load_missing_file_raises(tmp_path):
    with pytest.raises(CMELoaderError, match="file not found"):
        load_cme_voi(tmp_path / "does-not-exist.xls")


def test_load_no_filename_date_no_kwarg_raises(tmp_path):
    """Filename without YYYY-MM-DD pattern and no trade_date kwarg → raises."""
    if not SAMPLE_XLS.is_file():
        pytest.skip(f"sample file not present: {SAMPLE_XLS}")
    # Copy sample into a weird name in tmp_path
    bad_name = tmp_path / "VoiDetailsForProduct.xls"
    bad_name.write_bytes(SAMPLE_XLS.read_bytes())
    with pytest.raises(CMELoaderError, match="trade_date"):
        load_cme_voi(bad_name)


def test_load_dated_filename_no_kwarg(tmp_path):
    """Filename WITH date pattern: load without explicit trade_date kwarg."""
    if not SAMPLE_XLS.is_file():
        pytest.skip(f"sample file not present: {SAMPLE_XLS}")
    dated = tmp_path / "2026-04-22.xls"
    dated.write_bytes(SAMPLE_XLS.read_bytes())
    digest = load_cme_voi(dated)
    assert digest["trade_date"] == "2026-04-22"


# ---------------------------------------------------------------------------
# parse_and_save
# ---------------------------------------------------------------------------

def test_parse_and_save_roundtrip(tmp_path):
    if not SAMPLE_XLS.is_file():
        pytest.skip(f"sample file not present: {SAMPLE_XLS}")
    out = parse_and_save(SAMPLE_XLS, out_dir=tmp_path, trade_date=SAMPLE_DATE)
    assert out.is_file()
    assert out.suffix == ".json"
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["trade_date"] == "2026-04-22"
    assert "SR3" in loaded["options"]


def test_parse_and_save_default_dest_next_to_xls(tmp_path):
    if not SAMPLE_XLS.is_file():
        pytest.skip(f"sample file not present: {SAMPLE_XLS}")
    dated = tmp_path / "2026-04-22.xls"
    dated.write_bytes(SAMPLE_XLS.read_bytes())
    out = parse_and_save(dated)  # no out_dir → next to xls
    assert out == tmp_path / "2026-04-22.json"
    assert out.is_file()
