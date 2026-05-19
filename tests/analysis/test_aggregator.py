"""Tests for aggregator — the digest producer.

Synthetic CME digests are built inline rather than parsing real .xls files,
so the tests stay tight and the assertions are direct.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from kcp_structgen.analysis.aggregator import (
    TOP_K,
    _aggregate_oi_changes,
    _aggregate_volume,
    _rank_oi_changes,
    _rank_volume,
    build_digest,
)
from kcp_structgen.analysis.segmenter import Segment, segment_week

# Reference week: ISO 2026-W47, Mon 2026-11-16 → Fri 2026-11-20
MONDAY    = date(2026, 11, 16)
TUESDAY   = date(2026, 11, 17)
WEDNESDAY = date(2026, 11, 18)
THURSDAY  = date(2026, 11, 19)
FRIDAY    = date(2026, 11, 20)
WEEK = [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY]


# ---------------------------------------------------------------------------
# Synthetic data factories
# ---------------------------------------------------------------------------

def _mk_oi_digest(
    trade_date: date,
    *,
    sr3: dict | None = None,
    q0:  dict | None = None,
    futures: dict | None = None,
) -> dict:
    """Build a single daily CME digest with arbitrary nested option blocks.

    `sr3` and `q0` are nested {expiry: {'calls': [...], 'puts': [...]}} dicts.
    Each strike row is {strike, volume, oi, oi_change}.
    """
    options: dict[str, dict] = {}
    if sr3:
        options["SR3"] = sr3
    if q0:
        options["0Q"] = q0
    return {
        "trade_date":  trade_date.isoformat(),
        "futures":     futures or {},
        "options":     options,
    }


def _strike(s, oi, change, vol=0):
    return {"strike": s, "volume": vol, "oi": oi, "oi_change": change}


def _flow_row(d: date, note: str, **kw):
    base = {
        "date":      d.isoformat(),
        "raw_note":  note,
        "product":   None,
        "expiry":    None,
        "structure": None,
        "size":      None,
        "direction": None,
        "price":     None,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Skeleton: empty inputs
# ---------------------------------------------------------------------------

def test_empty_inputs_produces_skeleton():
    """No CME data, no flow, no events → valid digest with empty fields."""
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests={},
    )
    assert d["week"] == "2026-W47"
    assert d["trading_days_in_week"] == [d.isoformat() for d in WEEK]
    assert d["trading_days_with_data"] == []
    assert d["products"] == []
    assert d["events"] == []
    assert d["futures_oi"] == {}
    assert d["segments"] == []
    assert d["week_summary"]["top_oi_builds"] == []
    assert d["week_summary"]["top_oi_unwinds"] == []
    assert d["week_summary"]["top_volume"] == []
    assert d["prior_weeks"] == []
    assert d["warnings"] == []


def test_digest_is_json_serialisable():
    """Belt-and-braces: the dict must round-trip through json without errors."""
    d = build_digest(WEDNESDAY, daily_oi_digests={})
    payload = json.dumps(d)
    restored = json.loads(payload)
    assert restored["week"] == "2026-W47"


# ---------------------------------------------------------------------------
# OI aggregation primitives
# ---------------------------------------------------------------------------

def test_aggregate_oi_changes_sums_across_days():
    digests = {
        MONDAY.isoformat():  _mk_oi_digest(MONDAY,  sr3={"Z6": {"calls": [_strike(96.75, 50000, 3000)], "puts": []}}),
        TUESDAY.isoformat(): _mk_oi_digest(TUESDAY, sr3={"Z6": {"calls": [_strike(96.75, 55000, 5000)], "puts": []}}),
    }
    agg = _aggregate_oi_changes(digests, [MONDAY, TUESDAY])
    key = ("SR3", "Z6", 96.75, "call")
    assert agg[key]["delta_oi_sum"] == 8000
    assert agg[key]["at_close"] == 55000  # last-seen wins
    assert agg[key]["daily_deltas"] == [
        {"date": MONDAY.isoformat(),  "delta_oi": 3000},
        {"date": TUESDAY.isoformat(), "delta_oi": 5000},
    ]


def test_aggregate_oi_changes_splits_call_put():
    """Same strike on calls and puts → two separate buckets."""
    digests = {
        MONDAY.isoformat(): _mk_oi_digest(MONDAY, sr3={"Z6": {
            "calls": [_strike(96.75, 50000, 3000)],
            "puts":  [_strike(96.75, 20000, -1000)],
        }}),
    }
    agg = _aggregate_oi_changes(digests, [MONDAY])
    assert agg[("SR3", "Z6", 96.75, "call")]["delta_oi_sum"] == 3000
    assert agg[("SR3", "Z6", 96.75, "put")]["delta_oi_sum"] == -1000


def test_aggregate_oi_changes_missing_day_skipped():
    """A trading day with no digest is skipped without error."""
    digests = {
        MONDAY.isoformat(): _mk_oi_digest(MONDAY, sr3={"Z6": {"calls": [_strike(96.75, 50000, 3000)], "puts": []}}),
        # Tuesday missing entirely
    }
    agg = _aggregate_oi_changes(digests, [MONDAY, TUESDAY])
    assert agg[("SR3", "Z6", 96.75, "call")]["delta_oi_sum"] == 3000


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def test_rank_oi_changes_top_k_by_absolute_value():
    """Ranking is by |delta_oi_sum|; negative entries can outrank smaller positives."""
    agg = {
        ("SR3", "Z6", 96.75, "call"): {"delta_oi_sum":  5000, "at_close": 50000, "daily_deltas": []},
        ("SR3", "Z6", 96.87, "call"): {"delta_oi_sum": -8000, "at_close": 40000, "daily_deltas": []},
        ("SR3", "Z6", 96.62, "put"):  {"delta_oi_sum":  3000, "at_close": 30000, "daily_deltas": []},
    }
    ranked = _rank_oi_changes(agg, k=10)
    assert [r["strike"] for r in ranked] == [96.87, 96.75, 96.62]
    assert ranked[0]["delta_oi_sum"] == -8000   # sign preserved
    assert ranked[0]["rank"] == 1


def test_rank_oi_changes_excludes_zero_delta():
    agg = {
        ("SR3", "Z6", 96.75, "call"): {"delta_oi_sum": 0,    "at_close": 50000, "daily_deltas": []},
        ("SR3", "Z6", 96.87, "call"): {"delta_oi_sum": 1000, "at_close": 40000, "daily_deltas": []},
    }
    ranked = _rank_oi_changes(agg, k=10)
    assert len(ranked) == 1
    assert ranked[0]["strike"] == 96.87


def test_rank_oi_changes_cross_product_competition():
    """SR3 and 0Q strikes share one top-10 list."""
    agg = {
        ("SR3", "Z6", 96.75, "call"): {"delta_oi_sum":  5000, "at_close": 50000, "daily_deltas": []},
        ("0Q",  "Z6", 96.75, "call"): {"delta_oi_sum":  9000, "at_close": 30000, "daily_deltas": []},
    }
    ranked = _rank_oi_changes(agg, k=10)
    # 0Q wins on |delta|
    assert ranked[0]["product"] == "0Q"
    assert ranked[1]["product"] == "SR3"


def test_rank_oi_changes_caps_at_k():
    agg = {
        ("SR3", "Z6", 96.0 + i*0.01, "call"): {
            "delta_oi_sum": 1000 - i,   # all positive, strictly decreasing
            "at_close":     10000,
            "daily_deltas": [],
        }
        for i in range(20)
    }
    ranked = _rank_oi_changes(agg, k=10)
    assert len(ranked) == 10
    assert ranked[0]["delta_oi_sum"] == 1000
    assert ranked[-1]["delta_oi_sum"] == 991


def test_rank_oi_changes_tie_breaker_stable():
    """Equal |delta| → ordered by (product, expiry, strike, type) ascending."""
    agg = {
        ("SR3", "Z6", 96.87, "put"):  {"delta_oi_sum":  5000, "at_close": 0, "daily_deltas": []},
        ("SR3", "Z6", 96.75, "call"): {"delta_oi_sum":  5000, "at_close": 0, "daily_deltas": []},
        ("0Q",  "Z6", 96.75, "call"): {"delta_oi_sum":  5000, "at_close": 0, "daily_deltas": []},
    }
    ranked = _rank_oi_changes(agg, k=10)
    # 0Q < SR3 lexicographically; for SR3, 96.75 call < 96.87 put
    keys = [(r["product"], r["expiry"], r["strike"], r["type"]) for r in ranked]
    assert keys == [
        ("0Q",  "Z6", 96.75, "call"),
        ("SR3", "Z6", 96.75, "call"),
        ("SR3", "Z6", 96.87, "put"),
    ]


def test_rank_volume_basic():
    vol_agg = {
        ("SR3", "Z6", 96.75, "call"): {"volume_sum": 1000, "daily_volumes": []},
        ("SR3", "Z6", 96.87, "call"): {"volume_sum": 3000, "daily_volumes": []},
        ("SR3", "Z6", 96.62, "put"):  {"volume_sum":    0, "daily_volumes": []},
    }
    ranked = _rank_volume(vol_agg, k=10)
    assert [r["strike"] for r in ranked] == [96.87, 96.75]  # zero excluded


# ---------------------------------------------------------------------------
# Segment building inside the full digest
# ---------------------------------------------------------------------------

def _two_event_week_digests() -> dict[str, dict]:
    """A small but realistic synthetic week of CME data spanning Mon-Fri.

    Z6 96.75 calls: build Mon/Tue, partially unwind Wed, more unwind Thu.
    Z6 96.50 puts:  build Wed/Thu (post-event rotation).
    M7 96.00 calls: small build Mon-Fri.
    """
    return {
        MONDAY.isoformat(): _mk_oi_digest(MONDAY, sr3={
            "Z6": {
                "calls": [_strike(96.75, 50000, 3000, vol=1500)],
                "puts":  [_strike(96.50, 30000,    0, vol=  100)],
            },
            "M7": {
                "calls": [_strike(96.00, 10000,  500, vol=  200)],
                "puts":  [],
            },
        }),
        TUESDAY.isoformat(): _mk_oi_digest(TUESDAY, sr3={
            "Z6": {
                "calls": [_strike(96.75, 55000, 5000, vol=2500)],
                "puts":  [_strike(96.50, 30200,  200, vol=  150)],
            },
            "M7": {
                "calls": [_strike(96.00, 10500,  500, vol=  100)],
                "puts":  [],
            },
        }),
        WEDNESDAY.isoformat(): _mk_oi_digest(WEDNESDAY, sr3={
            "Z6": {
                "calls": [_strike(96.75, 50000, -5000, vol=8000)],   # CPI day; big unwind
                "puts":  [_strike(96.50, 35000,  4800, vol=3000)],   # rotation
            },
            "M7": {
                "calls": [_strike(96.00, 10800,  300, vol=  150)],
                "puts":  [],
            },
        }),
        THURSDAY.isoformat(): _mk_oi_digest(THURSDAY, sr3={
            "Z6": {
                "calls": [_strike(96.75, 48000, -2000, vol=2500)],
                "puts":  [_strike(96.50, 38000,  3000, vol=2000)],
            },
            "M7": {
                "calls": [_strike(96.00, 11000,  200, vol=  100)],
                "puts":  [],
            },
        }),
        FRIDAY.isoformat(): _mk_oi_digest(FRIDAY, sr3={
            "Z6": {
                "calls": [_strike(96.75, 48000,    0, vol=1000)],
                "puts":  [_strike(96.50, 39000, 1000, vol= 800)],
            },
            "M7": {
                "calls": [_strike(96.00, 11500,  500, vol=  200)],
                "puts":  [],
            },
        }),
    }


def test_no_events_emits_week_flat_single_segment():
    digests = _two_event_week_digests()
    segs = segment_week(WEDNESDAY, [])    # no events
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests=digests,
        segments=segs,
    )
    assert len(d["segments"]) == 1
    assert d["segments"][0]["name"] == "week_flat"
    assert len(d["segments"][0]["trading_days"]) == 5


def test_wednesday_cpi_three_segments_with_oi_in_each():
    """Per spec §9: pre/post segments are single-day EOD snapshots (the
    prior / next trading day), not multi-day windows."""
    digests = _two_event_week_digests()
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(WEDNESDAY, [cpi])
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests=digests,
        events=[cpi],
        segments=segs,
    )
    assert [s["name"] for s in d["segments"]] == ["pre_CPI", "event_day_CPI", "post_CPI"]
    pre, event_day, post = d["segments"]

    # Pre = Tuesday only (the trading day before Wed CPI). Z6 96.75c +5000.
    assert pre["trading_days"] == [TUESDAY.isoformat()]
    pre_top = pre["top_oi_changes"][0]
    assert pre_top["product"] == "SR3"
    assert pre_top["expiry"] == "Z6"
    assert pre_top["strike"] == 96.75
    assert pre_top["type"] == "call"
    assert pre_top["delta_oi_sum"] == 5000
    assert pre_top["daily_deltas"] == [{"date": TUESDAY.isoformat(), "delta_oi": 5000}]

    # Event day = Wed: Z6 96.75c -5000 (largest |delta|), 96.50p +4800
    assert event_day["trading_days"] == [WEDNESDAY.isoformat()]
    deltas_event = {(r["strike"], r["type"]): r["delta_oi_sum"]
                    for r in event_day["top_oi_changes"]}
    assert deltas_event[(96.75, "call")] == -5000
    assert deltas_event[(96.50, "put")]  ==  4800

    # Post = Thursday only.
    assert post["trading_days"] == [THURSDAY.isoformat()]
    post_strikes = {(r["strike"], r["type"]) for r in post["top_oi_changes"]}
    assert (96.50, "put") in post_strikes
    assert (96.75, "call") in post_strikes


def test_anchor_event_fields_populated():
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(WEDNESDAY, [cpi])
    d = build_digest(WEDNESDAY, daily_oi_digests={}, events=[cpi], segments=segs)
    for s in d["segments"]:
        assert s["anchor_event_date"] == WEDNESDAY.isoformat()
        assert s["anchor_matcher"] == "CPI"


def test_anchor_fields_none_for_week_flat():
    segs = segment_week(WEDNESDAY, [])
    d = build_digest(WEDNESDAY, daily_oi_digests={}, segments=segs)
    assert d["segments"][0]["anchor_event_date"] is None
    assert d["segments"][0]["anchor_matcher"] is None


def test_empty_segment_trading_days_yields_empty_lists():
    """Monday event → pre_CPI has trading_days=[], top lists empty."""
    cpi = {"date": MONDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(MONDAY, [cpi])
    d = build_digest(
        MONDAY,
        daily_oi_digests=_two_event_week_digests(),
        events=[cpi],
        segments=segs,
    )
    pre = d["segments"][0]
    assert pre["name"] == "pre_CPI"
    assert pre["trading_days"] == []
    assert pre["top_oi_changes"] == []
    assert pre["top_volume"] == []
    assert pre["flow_notes"] == []
    assert pre["client_trades"] == []


# ---------------------------------------------------------------------------
# Flow + client trades
# ---------------------------------------------------------------------------

def test_flow_notes_filtered_by_segment_days():
    """Pre/post segments are single trading days (per spec §9). A flow note
    on Monday is outside pre_CPI (which is Tuesday only) for a Wed CPI."""
    digests = _two_event_week_digests()
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(WEDNESDAY, [cpi])
    flow = [
        _flow_row(MONDAY,    "Mon — not in any CPI segment"),     # outside (pre = Tue only)
        _flow_row(TUESDAY,   "Z6 calls bid all morning"),         # pre_CPI
        _flow_row(WEDNESDAY, "ppr covered Z6 calls into close"),  # event_day_CPI
        _flow_row(THURSDAY,  "Z6 96.50 puts bid"),                # post_CPI
        _flow_row(FRIDAY,    "Fri — not in any CPI segment"),     # outside (post = Thu only)
    ]
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests=digests,
        flow_rows=flow,
        events=[cpi],
        segments=segs,
    )
    pre, event_day, post = d["segments"]
    assert pre["flow_notes"]       == ["Z6 calls bid all morning"]
    assert event_day["flow_notes"] == ["ppr covered Z6 calls into close"]
    assert post["flow_notes"]      == ["Z6 96.50 puts bid"]


def test_client_trades_kept_separate_from_flow():
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(WEDNESDAY, [cpi])
    flow = [_flow_row(WEDNESDAY, "street: Z6 covered")]
    client = [_flow_row(WEDNESDAY, "KCP: bought Z6 96.50 p 2k for client X")]
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests=_two_event_week_digests(),
        flow_rows=flow,
        client_rows=client,
        events=[cpi],
        segments=segs,
    )
    event_day = d["segments"][1]
    assert event_day["flow_notes"]    == ["street: Z6 covered"]
    assert event_day["client_trades"] == ["KCP: bought Z6 96.50 p 2k for client X"]


# ---------------------------------------------------------------------------
# daily_commentary attachment
# ---------------------------------------------------------------------------

def _commentary_for(d: date, headlines: list[str], commentary: str = "n/a") -> dict:
    return {
        "date":       d.isoformat(),
        "sources":    ["itc_us_morning.docx"],
        "headlines":  headlines,
        "commentary": commentary,
    }


def test_daily_commentary_filtered_to_segment_days():
    """Pre/post segments are single days; commentary entries for other days
    must not leak into them."""
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(WEDNESDAY, [cpi])
    commentary = {
        MONDAY.isoformat():    _commentary_for(MONDAY,    ["MNI: Mon"]),
        TUESDAY.isoformat():   _commentary_for(TUESDAY,   ["MNI: Tue"]),
        WEDNESDAY.isoformat(): _commentary_for(WEDNESDAY, ["MNI: Wed"]),
        THURSDAY.isoformat():  _commentary_for(THURSDAY,  ["MNI: Thu"]),
        FRIDAY.isoformat():    _commentary_for(FRIDAY,    ["MNI: Fri"]),
    }
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests={},
        events=[cpi],
        segments=segs,
        daily_commentary=commentary,
    )
    pre, event_day, post = d["segments"]
    # pre_CPI = Tue only
    assert list(pre["daily_commentary"].keys()) == [TUESDAY.isoformat()]
    # event_day_CPI = Wed only
    assert list(event_day["daily_commentary"].keys()) == [WEDNESDAY.isoformat()]
    # post_CPI = Thu only
    assert list(post["daily_commentary"].keys()) == [THURSDAY.isoformat()]


def test_daily_commentary_absent_for_days_without_entry():
    """If commentary dict has fewer days than segment.trading_days, only
    the days that exist appear in the segment's daily_commentary."""
    segs = segment_week(WEDNESDAY, [])  # week_flat = all 5 days
    commentary = {
        WEDNESDAY.isoformat(): _commentary_for(WEDNESDAY, ["MNI: only Wed has data"]),
    }
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests={},
        segments=segs,
        daily_commentary=commentary,
    )
    seg = d["segments"][0]
    assert list(seg["daily_commentary"].keys()) == [WEDNESDAY.isoformat()]
    # Mon/Tue/Thu/Fri are NOT padded with empty stubs
    assert MONDAY.isoformat()   not in seg["daily_commentary"]
    assert FRIDAY.isoformat()   not in seg["daily_commentary"]


def test_daily_commentary_default_empty():
    """Not passing daily_commentary at all → every segment has empty dict."""
    segs = segment_week(WEDNESDAY, [])
    d = build_digest(WEDNESDAY, daily_oi_digests={}, segments=segs)
    assert d["segments"][0]["daily_commentary"] == {}


def test_daily_commentary_preserves_entry_shape():
    """The full {date, sources, headlines, commentary} dict survives the
    attach step intact — we don't strip fields."""
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "surprise": "hot"}
    segs = segment_week(WEDNESDAY, [cpi])
    entry = {
        "date":       WEDNESDAY.isoformat(),
        "sources":    ["itc_us_morning.docx", "mni_european_open.docx"],
        "headlines":  ["ITC: A", "MNI: B"],
        "commentary": "Quiet session pre-CPI.",
    }
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests={},
        events=[cpi],
        segments=segs,
        daily_commentary={WEDNESDAY.isoformat(): entry},
    )
    event_day = d["segments"][1]
    assert event_day["daily_commentary"][WEDNESDAY.isoformat()] == entry


# ---------------------------------------------------------------------------
# Week summary
# ---------------------------------------------------------------------------

def test_week_summary_builds_descending_positive():
    digests = _two_event_week_digests()
    d = build_digest(WEDNESDAY, daily_oi_digests=digests)
    builds = d["week_summary"]["top_oi_builds"]
    # Every entry positive, sorted descending
    assert all(b["delta_oi_sum"] > 0 for b in builds)
    deltas = [b["delta_oi_sum"] for b in builds]
    assert deltas == sorted(deltas, reverse=True)
    # 96.50 p week sum = 0 + 200 + 4800 + 3000 + 1000 = 9000 — should top the list
    assert builds[0]["strike"] == 96.50
    assert builds[0]["type"] == "put"
    assert builds[0]["delta_oi_sum"] == 9000


def test_week_summary_unwinds_ascending_negative():
    digests = _two_event_week_digests()
    d = build_digest(WEDNESDAY, daily_oi_digests=digests)
    unwinds = d["week_summary"]["top_oi_unwinds"]
    # Z6 96.75 c week sum = 3000 + 5000 - 5000 - 2000 + 0 = 1000  → POSITIVE
    # So Z6 96.75 c should NOT appear in unwinds.
    assert all(u["delta_oi_sum"] < 0 for u in unwinds)


def test_week_summary_builds_and_unwinds_disjoint():
    digests = _two_event_week_digests()
    d = build_digest(WEDNESDAY, daily_oi_digests=digests)
    build_keys   = {(b["product"], b["expiry"], b["strike"], b["type"])
                    for b in d["week_summary"]["top_oi_builds"]}
    unwind_keys  = {(u["product"], u["expiry"], u["strike"], u["type"])
                    for u in d["week_summary"]["top_oi_unwinds"]}
    assert build_keys.isdisjoint(unwind_keys)


# ---------------------------------------------------------------------------
# Futures rollup
# ---------------------------------------------------------------------------

def test_futures_rollup_quarterlies_only_and_sums_correctly():
    digests = {
        MONDAY.isoformat():  _mk_oi_digest(MONDAY,  futures={
            "M6": {"oi": 1000, "oi_change": 100, "volume": 5000},
            "U6": {"oi": 2000, "oi_change": -50, "volume": 1000},
            "K6": {"oi": 500,  "oi_change":  10, "volume":  100},  # non-quarterly → dropped
        }),
        TUESDAY.isoformat(): _mk_oi_digest(TUESDAY, futures={
            "M6": {"oi": 1100, "oi_change": 100, "volume": 2000},
        }),
    }
    d = build_digest(WEDNESDAY, daily_oi_digests=digests)
    fut = d["futures_oi"]
    # K6 must be filtered out
    assert "K6" not in fut
    assert "M6" in fut
    # M6 sums: change = 100+100 = 200, vol = 5000+2000 = 7000, oi_close = last seen = 1100
    assert fut["M6"]["oi_change_week"] == 200
    assert fut["M6"]["volume_week"]    == 7000
    assert fut["M6"]["oi_close"]       == 1100
    # U6 single-day: oi_close from Mon (2000)
    assert fut["U6"]["oi_close"] == 2000
    assert fut["U6"]["oi_change_week"] == -50


# ---------------------------------------------------------------------------
# Events + FOMC tone
# ---------------------------------------------------------------------------

def test_events_passed_through_with_fomc_tone():
    cpi = {"date": WEDNESDAY.isoformat(), "matcher": "CPI",
           "event_name": "CPI YoY", "previous": 2.7, "estimate": 2.9, "actual": 3.2,
           "surprise": "hot", "impact": "High"}
    fomc = {"date": THURSDAY.isoformat(), "matcher": "FOMC",
            "event_name": "FOMC Rate Decision",
            "previous": 4.25, "estimate": 4.00, "actual": 4.00,
            "surprise": None, "impact": "High",
            "fomc_tone_summary": "Hawkish: removed 'data-dependent' language."}
    d = build_digest(WEDNESDAY, daily_oi_digests={}, events=[cpi, fomc])
    assert d["events"] == [cpi, fomc]
    assert d["events"][1]["fomc_tone_summary"].startswith("Hawkish")


# ---------------------------------------------------------------------------
# Pass-throughs
# ---------------------------------------------------------------------------

def test_warnings_passed_through():
    d = build_digest(
        WEDNESDAY,
        daily_oi_digests={},
        warnings=["CME file missing for 2026-11-18"],
    )
    assert d["warnings"] == ["CME file missing for 2026-11-18"]


def test_prior_weeks_passed_through():
    prior = [
        {"week": "2026-W46", "digest": {"week": "2026-W46"}, "headlines": ["x"]},
    ]
    d = build_digest(WEDNESDAY, daily_oi_digests={}, prior_weeks=prior)
    assert d["prior_weeks"] == prior


def test_trading_days_with_data_reflects_available_files():
    digests = {
        MONDAY.isoformat():   _mk_oi_digest(MONDAY),
        THURSDAY.isoformat(): _mk_oi_digest(THURSDAY),
    }
    d = build_digest(WEDNESDAY, daily_oi_digests=digests)
    assert d["trading_days_with_data"] == sorted([MONDAY.isoformat(), THURSDAY.isoformat()])


def test_products_detected_from_digests():
    digests = {
        MONDAY.isoformat(): _mk_oi_digest(MONDAY,
                                          sr3={"Z6": {"calls": [], "puts": []}},
                                          q0={"Z6":  {"calls": [], "puts": []}}),
    }
    d = build_digest(WEDNESDAY, daily_oi_digests=digests)
    assert d["products"] == ["0Q", "SR3"]
