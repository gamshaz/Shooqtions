"""Tests for segmenter — week × events → list of segments.

Reference week: 16-20 November 2026 (Mon-Fri). ISO week 2026-W47.
"""

from __future__ import annotations

from datetime import date

import pytest

from kcp_structgen.analysis.segmenter import Segment, segment_week

MONDAY    = date(2026, 11, 16)
TUESDAY   = date(2026, 11, 17)
WEDNESDAY = date(2026, 11, 18)
THURSDAY  = date(2026, 11, 19)
FRIDAY    = date(2026, 11, 20)
ALL_WEEKDAYS = [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY]


def _ev(d: date, matcher: str, name: str = None, surprise: str = None) -> dict:
    return {
        "date":       d.isoformat(),
        "matcher":    matcher,
        "event_name": name or f"{matcher} test",
        "surprise":   surprise,
    }


# ---------------------------------------------------------------------------
# Happy path: tier-1 event on Wednesday → three segments
# ---------------------------------------------------------------------------

def test_wednesday_cpi_three_segments():
    events = [_ev(WEDNESDAY, "CPI", "Consumer Price Index YoY", "hot")]
    segs = segment_week(WEDNESDAY, events)
    assert len(segs) == 3

    pre = segs[0]
    assert pre.name == "pre_CPI"
    assert pre.trading_days == [TUESDAY]
    assert pre.anchor_event is events[0]

    event_day = segs[1]
    assert event_day.name == "event_day_CPI"
    assert event_day.trading_days == [WEDNESDAY]

    post = segs[2]
    assert post.name == "post_CPI"
    assert post.trading_days == [THURSDAY]


# ---------------------------------------------------------------------------
# Edge cases on event placement within the week
# ---------------------------------------------------------------------------

def test_monday_event_empty_pre():
    events = [_ev(MONDAY, "CPI")]
    segs = segment_week(MONDAY, events)
    assert [s.trading_days for s in segs] == [[], [MONDAY], [TUESDAY]]


def test_friday_event_empty_post():
    events = [_ev(FRIDAY, "NFP")]
    segs = segment_week(FRIDAY, events)
    assert [s.trading_days for s in segs] == [[THURSDAY], [FRIDAY], []]


def test_weekend_event_ignored():
    """Saturday/Sunday events are dropped — no trading day to anchor to."""
    saturday = date(2026, 11, 21)
    events = [_ev(saturday, "CPI")]
    segs = segment_week(WEDNESDAY, events)
    assert len(segs) == 1
    assert segs[0].name == "week_flat"


# ---------------------------------------------------------------------------
# No tier-1 events
# ---------------------------------------------------------------------------

def test_no_tier1_events_one_flat_segment():
    segs = segment_week(WEDNESDAY, [])
    assert len(segs) == 1
    assert segs[0].name == "week_flat"
    assert segs[0].trading_days == ALL_WEEKDAYS
    assert segs[0].anchor_event is None


def test_only_tier2_events_still_flat():
    """Tier-2 events (ADP, PPI, etc.) don't generate segments."""
    events = [
        _ev(WEDNESDAY, "ADP",          "ADP Employment Change"),
        _ev(THURSDAY,  "JOBLESS",      "Initial Jobless Claims"),
        _ev(FRIDAY,    "RETAIL_SALES", "Retail Sales MoM"),
    ]
    segs = segment_week(WEDNESDAY, events)
    assert len(segs) == 1
    assert segs[0].name == "week_flat"


def test_unmatched_events_ignored():
    """Events with matcher=None pass through harmlessly."""
    events = [
        {"date": WEDNESDAY.isoformat(), "matcher": None, "event_name": "Fed Speech"},
    ]
    segs = segment_week(WEDNESDAY, events)
    assert len(segs) == 1
    assert segs[0].name == "week_flat"


# ---------------------------------------------------------------------------
# Multiple tier-1 events in the same week
# ---------------------------------------------------------------------------

def test_two_tier1_events_six_segments_with_overlap():
    """CPI Wed + NFP Fri → 6 segments. Thursday is both post_CPI and pre_NFP."""
    cpi = _ev(WEDNESDAY, "CPI", "CPI YoY",          "hot")
    nfp = _ev(FRIDAY,    "NFP", "Nonfarm Payrolls", "inline")
    segs = segment_week(WEDNESDAY, [cpi, nfp])
    assert len(segs) == 6
    names = [s.name for s in segs]
    assert names == [
        "pre_CPI", "event_day_CPI", "post_CPI",
        "pre_NFP", "event_day_NFP", "post_NFP",
    ]
    # Thursday in both post_CPI and pre_NFP
    post_cpi = next(s for s in segs if s.name == "post_CPI")
    pre_nfp  = next(s for s in segs if s.name == "pre_NFP")
    assert post_cpi.trading_days == [THURSDAY]
    assert pre_nfp.trading_days  == [THURSDAY]


def test_events_sorted_by_date():
    """Even if events come in reverse order, segments emerge chronologically."""
    nfp = _ev(FRIDAY,    "NFP")
    cpi = _ev(WEDNESDAY, "CPI")
    segs = segment_week(WEDNESDAY, [nfp, cpi])  # reversed input
    names = [s.name for s in segs]
    assert names[0:3] == ["pre_CPI", "event_day_CPI", "post_CPI"]
    assert names[3:6] == ["pre_NFP", "event_day_NFP", "post_NFP"]


# ---------------------------------------------------------------------------
# Defensive: events outside the week
# ---------------------------------------------------------------------------

def test_event_outside_week_dropped():
    """An event whose date falls outside this Mon-Fri is silently ignored."""
    last_week_cpi = _ev(date(2026, 11, 11), "CPI")
    this_week_event = _ev(WEDNESDAY, "NFP")
    segs = segment_week(WEDNESDAY, [last_week_cpi, this_week_event])
    names = [s.name for s in segs]
    assert "pre_CPI" not in names
    assert "pre_NFP" in names


def test_bad_event_date_ignored():
    """An event with garbage in its date field doesn't crash the segmenter."""
    bad = {"matcher": "CPI", "date": "not-a-date", "event_name": "?"}
    segs = segment_week(WEDNESDAY, [bad])
    assert len(segs) == 1
    assert segs[0].name == "week_flat"


def test_event_missing_date_ignored():
    bad = {"matcher": "CPI", "event_name": "no date"}
    segs = segment_week(WEDNESDAY, [bad])
    assert segs[0].name == "week_flat"


# ---------------------------------------------------------------------------
# anchor_event reference identity
# ---------------------------------------------------------------------------

def test_anchor_event_is_same_object():
    """The Segment.anchor_event must be the same dict instance the caller
    passed in (downstream code may mutate it for tagging without copying)."""
    cpi = _ev(WEDNESDAY, "CPI")
    segs = segment_week(WEDNESDAY, [cpi])
    for s in segs:
        assert s.anchor_event is cpi


# ---------------------------------------------------------------------------
# Mixed tier-1 + tier-2: only tier-1 makes segments
# ---------------------------------------------------------------------------

def test_tier2_in_same_week_as_tier1_does_not_extra_segment():
    cpi = _ev(WEDNESDAY, "CPI")
    adp = _ev(THURSDAY,  "ADP")  # tier-2
    segs = segment_week(WEDNESDAY, [cpi, adp])
    assert len(segs) == 3
    assert {s.name for s in segs} == {"pre_CPI", "event_day_CPI", "post_CPI"}
