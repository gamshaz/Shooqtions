"""Tests for event_matcher.

Covers regex matching across known FMP/Bloomberg name variants, tier
membership, and FOMC dedupe within a 6-hour window.
"""

from __future__ import annotations

import pytest

from kcp_structgen.analysis.event_matcher import (
    TIER_1,
    TIER_2,
    dedupe_fomc,
    match_event,
    tag_events,
    tier_of,
)


# ---------------------------------------------------------------------------
# match_event — tier-1 events with their FMP / Bloomberg variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Consumer Price Index YoY",
    "Consumer Price Index (YoY)",
    "CPI YoY",
    "CPI MoM",
])
def test_match_cpi_variants(name):
    assert match_event(name) == "CPI"


def test_match_core_cpi_beats_cpi():
    assert match_event("Core CPI YoY") == "CORE_CPI"
    assert match_event("Core Consumer Price Index") == "CORE_CPI"


@pytest.mark.parametrize("name", [
    "Non Farm Payrolls",
    "Nonfarm Payrolls",
    "Non-Farm Payrolls",
    "Change in Nonfarm Payrolls",
])
def test_match_nfp_variants(name):
    assert match_event(name) == "NFP"


def test_match_core_pce_beats_pce():
    assert match_event("Core PCE Price Index") == "CORE_PCE"
    assert match_event("PCE Price Index") == "PCE"


def test_match_fomc_variants():
    assert match_event("FOMC Statement") == "FOMC"
    assert match_event("FOMC Rate Decision") == "FOMC"
    assert match_event("Federal Funds Target Rate") == "FOMC"
    assert match_event("Fed Interest Rate Decision") == "FOMC"


def test_match_gdp():
    assert match_event("GDP QoQ Annualized") == "GDP"
    assert match_event("Gross Domestic Product") == "GDP"


def test_match_ism_split():
    assert match_event("ISM Manufacturing PMI") == "ISM_MFG"
    assert match_event("ISM Services PMI") == "ISM_SVC"
    assert match_event("ISM Non-Manufacturing PMI") == "ISM_SVC"


# ---------------------------------------------------------------------------
# Tier 2
# ---------------------------------------------------------------------------

def test_match_tier2():
    assert match_event("ADP Employment Change") == "ADP"
    assert match_event("PPI MoM") == "PPI"
    assert match_event("Producer Price Index YoY") == "PPI"
    assert match_event("Retail Sales MoM") == "RETAIL_SALES"
    assert match_event("Initial Jobless Claims") == "JOBLESS"
    assert match_event("Continuing Jobless Claims") == "JOBLESS"
    assert match_event("JOLTS Job Openings") == "JOLTS"
    assert match_event("S&P Global US Manufacturing PMI") == "SP_PMI_MFG"
    assert match_event("S&P Global US Services PMI") == "SP_PMI_SVC"
    assert match_event("S&P Global Composite PMI") == "SP_PMI_SVC"


# ---------------------------------------------------------------------------
# Non-matches and edge cases
# ---------------------------------------------------------------------------

def test_no_match_unknown_event():
    assert match_event("Random Speech by Mr X") is None
    assert match_event("EU Inflation") is None  # not a US-specific event


def test_no_match_empty_or_none():
    assert match_event(None) is None
    assert match_event("") is None


def test_case_insensitive():
    assert match_event("nonfarm payrolls") == "NFP"
    assert match_event("CONSUMER PRICE INDEX") == "CPI"


# ---------------------------------------------------------------------------
# Tier membership
# ---------------------------------------------------------------------------

def test_tier_of():
    assert tier_of("FOMC") == "tier1"
    assert tier_of("CPI") == "tier1"
    assert tier_of("NFP") == "tier1"
    assert tier_of("ADP") == "tier2"
    assert tier_of("JOBLESS") == "tier2"
    assert tier_of(None) is None
    assert tier_of("UNKNOWN") is None


def test_tier_sets_disjoint():
    assert TIER_1.isdisjoint(TIER_2)


# ---------------------------------------------------------------------------
# tag_events
# ---------------------------------------------------------------------------

def test_tag_events_in_place():
    events = [
        {"event_name": "CPI YoY"},
        {"event_name": "Non Farm Payrolls"},
        {"event_name": "Some Speech"},
    ]
    tag_events(events)
    assert events[0]["matcher"] == "CPI"
    assert events[1]["matcher"] == "NFP"
    assert events[2]["matcher"] is None


def test_tag_events_idempotent():
    events = [{"event_name": "FOMC Statement", "matcher": "FOMC"}]
    tag_events(events)
    assert events[0]["matcher"] == "FOMC"


# ---------------------------------------------------------------------------
# FOMC dedupe
# ---------------------------------------------------------------------------

def test_dedupe_fomc_collapses_same_day():
    """Same-day FOMC rows (statement + rate decision + SEP + presser) collapse."""
    events = [
        {"date": "2026-11-18T14:00:00", "event_name": "FOMC Statement"},
        {"date": "2026-11-18T14:00:00", "event_name": "FOMC Rate Decision"},
        {"date": "2026-11-18T14:30:00", "event_name": "FOMC Economic Projections"},
        {"date": "2026-11-18T18:30:00", "event_name": "FOMC Press Conference"},
    ]
    tag_events(events)
    out = dedupe_fomc(events)
    fomc_rows = [e for e in out if e.get("matcher") == "FOMC"]
    assert len(fomc_rows) == 1


def test_dedupe_fomc_keeps_separate_meetings():
    """Two different meetings (8 weeks apart) must not collapse."""
    events = [
        {"date": "2026-09-16T14:00:00", "event_name": "FOMC Statement"},
        {"date": "2026-11-18T14:00:00", "event_name": "FOMC Statement"},
    ]
    tag_events(events)
    out = dedupe_fomc(events)
    fomc_rows = [e for e in out if e.get("matcher") == "FOMC"]
    assert len(fomc_rows) == 2


def test_dedupe_fomc_does_not_touch_other_events():
    events = [
        {"date": "2026-11-18T13:30:00", "event_name": "CPI YoY"},
        {"date": "2026-11-18T14:00:00", "event_name": "FOMC Statement"},
        {"date": "2026-11-18T14:00:00", "event_name": "FOMC Rate Decision"},
        {"date": "2026-11-20T13:30:00", "event_name": "Non Farm Payrolls"},
    ]
    tag_events(events)
    out = dedupe_fomc(events)
    # CPI + NFP preserved; FOMC collapsed from 2 to 1
    matchers = [e.get("matcher") for e in out]
    assert matchers.count("CPI") == 1
    assert matchers.count("NFP") == 1
    assert matchers.count("FOMC") == 1


def test_dedupe_fomc_empty():
    assert dedupe_fomc([]) == []


def test_dedupe_fomc_single_fomc():
    events = [{"date": "2026-11-18T14:00:00", "event_name": "FOMC Statement"}]
    tag_events(events)
    assert len(dedupe_fomc(events)) == 1


def test_dedupe_fomc_window_boundary():
    """Two FOMC rows exactly 6 hours apart should be collapsed (boundary inclusive)."""
    events = [
        {"date": "2026-11-18T14:00:00", "event_name": "FOMC Statement"},
        {"date": "2026-11-18T20:00:00", "event_name": "FOMC Press Conference"},
    ]
    tag_events(events)
    out = dedupe_fomc(events, window_hours=6)
    fomc_rows = [e for e in out if e.get("matcher") == "FOMC"]
    assert len(fomc_rows) == 1
