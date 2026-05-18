"""Tests for classifier — surprise bands per spec §8.

User-dictated: NFP inline ±5k. Other bands are derived per spec.
"""

from __future__ import annotations

import pytest

from kcp_structgen.analysis.classifier import (
    INLINE_BANDS,
    classify_events,
    classify_surprise,
)


def _ev(matcher: str, estimate, actual) -> dict:
    return {"matcher": matcher, "estimate": estimate, "actual": actual}


# ---------------------------------------------------------------------------
# NFP (the user's anchor band)
# ---------------------------------------------------------------------------

def test_nfp_inline_at_5k():
    """NFP ±5k is inline (boundary inclusive)."""
    assert classify_surprise(_ev("NFP", 200_000, 205_000)) == "inline"
    assert classify_surprise(_ev("NFP", 200_000, 195_000)) == "inline"
    assert classify_surprise(_ev("NFP", 200_000, 200_000)) == "inline"


def test_nfp_hot_above_5k():
    assert classify_surprise(_ev("NFP", 200_000, 250_000)) == "hot"


def test_nfp_cold_below_5k():
    assert classify_surprise(_ev("NFP", 200_000, 150_000)) == "cold"


# ---------------------------------------------------------------------------
# CPI / PCE — percentage points
# ---------------------------------------------------------------------------

def test_cpi_inline_0_1pp():
    assert classify_surprise(_ev("CPI", 2.9, 3.0)) == "inline"
    assert classify_surprise(_ev("CPI", 2.9, 2.8)) == "inline"
    assert classify_surprise(_ev("CPI", 2.9, 2.9)) == "inline"


def test_cpi_hot():
    assert classify_surprise(_ev("CPI", 2.9, 3.2)) == "hot"


def test_cpi_cold():
    assert classify_surprise(_ev("CPI", 2.9, 2.6)) == "cold"


def test_core_cpi_separate_band():
    """CORE_CPI is its own matcher and uses its own (same-sized) band."""
    assert classify_surprise(_ev("CORE_CPI", 3.0, 3.0)) == "inline"
    assert classify_surprise(_ev("CORE_CPI", 3.0, 3.3)) == "hot"


def test_pce_classified():
    assert classify_surprise(_ev("PCE", 2.5, 2.8)) == "hot"
    assert classify_surprise(_ev("CORE_PCE", 2.5, 2.2)) == "cold"


# ---------------------------------------------------------------------------
# Growth and PMIs
# ---------------------------------------------------------------------------

def test_gdp_inline_at_0_1pp():
    assert classify_surprise(_ev("GDP", 2.0, 2.1)) == "inline"
    assert classify_surprise(_ev("GDP", 2.0, 2.5)) == "hot"


def test_ism_inline_at_0_3pt():
    assert classify_surprise(_ev("ISM_MFG", 50.0, 50.3)) == "inline"
    assert classify_surprise(_ev("ISM_MFG", 50.0, 51.0)) == "hot"
    assert classify_surprise(_ev("ISM_SVC", 55.0, 54.0)) == "cold"


def test_sp_pmi_uses_same_band_as_ism():
    assert INLINE_BANDS["SP_PMI_MFG"] == INLINE_BANDS["ISM_MFG"]
    assert classify_surprise(_ev("SP_PMI_MFG", 50.0, 50.2)) == "inline"


# ---------------------------------------------------------------------------
# Tier-2 jobs data
# ---------------------------------------------------------------------------

def test_adp_inline_at_10k():
    assert classify_surprise(_ev("ADP", 150_000, 160_000)) == "inline"
    assert classify_surprise(_ev("ADP", 150_000, 175_000)) == "hot"


def test_jobless_inline_at_5k():
    assert classify_surprise(_ev("JOBLESS", 210_000, 215_000)) == "inline"
    assert classify_surprise(_ev("JOBLESS", 210_000, 250_000)) == "hot"


def test_jolts_inline_at_100k():
    assert classify_surprise(_ev("JOLTS", 8_500_000, 8_400_000)) == "inline"
    assert classify_surprise(_ev("JOLTS", 8_500_000, 9_000_000)) == "hot"


# ---------------------------------------------------------------------------
# FOMC — never classified here
# ---------------------------------------------------------------------------

def test_fomc_returns_none():
    """FOMC is classified by the LLM from a statement diff, not numerically."""
    assert classify_surprise(_ev("FOMC", None, None)) is None


def test_fomc_returns_none_even_with_numbers():
    """If estimate/actual leak in by accident, FOMC still returns None."""
    assert classify_surprise(_ev("FOMC", 5.25, 5.50)) is None


# ---------------------------------------------------------------------------
# Missing / unclassifiable
# ---------------------------------------------------------------------------

def test_unknown_matcher_returns_none():
    assert classify_surprise(_ev("UNKNOWN_MATCHER", 1.0, 1.0)) is None


def test_missing_matcher_returns_none():
    assert classify_surprise({"estimate": 1.0, "actual": 1.0}) is None


def test_missing_estimate_returns_none():
    assert classify_surprise(_ev("CPI", None, 3.0)) is None


def test_missing_actual_returns_none():
    """Future events (no actual yet) cannot be classified."""
    assert classify_surprise(_ev("NFP", 200_000, None)) is None


def test_non_numeric_returns_none():
    """Robust to garbage in the data — never crash on a weird event."""
    assert classify_surprise(_ev("CPI", "two", 3.0)) is None
    assert classify_surprise(_ev("CPI", 2.9, "high")) is None


# ---------------------------------------------------------------------------
# classify_events: in-place tagging
# ---------------------------------------------------------------------------

def test_classify_events_in_place():
    events = [
        _ev("CPI", 2.9, 3.2),
        _ev("NFP", 200_000, 202_000),
        _ev("FOMC", None, None),
        _ev("UNKNOWN", 1, 1),
    ]
    classify_events(events)
    assert [e["surprise"] for e in events] == ["hot", "inline", None, None]


def test_classify_events_returns_same_list():
    events = [_ev("CPI", 2.9, 3.0)]
    assert classify_events(events) is events
