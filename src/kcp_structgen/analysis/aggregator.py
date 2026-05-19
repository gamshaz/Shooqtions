"""Aggregator — produce the structured digest the LLM consumes.

Pure function: no I/O, no LLM call, no exceptions in normal operation. Takes
the outputs of all upstream loaders (cme_loader, flow_loader, events_api,
event_matcher, classifier, fomc_scraper) plus the segmenter, returns one
JSON-serialisable dict.

Per spec §10 (frozen 2026-04-24) with revisions agreed in conversation:
  - No per-expiry block. One flat top-10 list per segment across both products.
  - top_oi_changes / top_volume entries carry a daily_deltas / daily_volumes
    array so the LLM can see intra-segment temporal pattern.
  - week_summary splits builds (most positive ΔOI) from unwinds (most negative).
  - Futures OI/volume rollup at top-level, quarterlies in scope only.
  - prior_weeks is the full digest of each prior week (recursion is fine).
  - FOMC tone summary lives on the FOMC event row as `fomc_tone_summary`.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from .events_api import week_label, week_window
from .segmenter import Segment

TOP_K = 10

# In-scope quarterlies for futures rollup (per spec §4.1).
QUARTERLY_LETTERS = ("H", "M", "U", "Z")
QUARTERLY_YEARS = ("6", "7")


# ---------------------------------------------------------------------------
# Helpers — keyed flattening + ranking
# ---------------------------------------------------------------------------

def _iter_strike_rows(
    daily_oi_digests: dict[str, dict],
    trading_days: list[date],
) -> Iterable[tuple[str, dict, str, str, str, dict]]:
    """Yield (iso_date, daily_digest, product, expiry, type, strike_row).

    `type` is 'call' or 'put'. `strike_row` is the raw {strike, volume, oi,
    oi_change} dict from the CME loader.

    Iteration order is stable: ascending day, then product order from the
    digest, then expiry order, then strikes as listed.
    """
    for d in trading_days:
        iso = d.isoformat()
        digest = daily_oi_digests.get(iso)
        if digest is None:
            continue
        options = digest.get("options", {})
        for product in options:
            for expiry, sides in options[product].items():
                for side_key, type_label in (("calls", "call"), ("puts", "put")):
                    for row in sides.get(side_key, []):
                        yield iso, digest, product, expiry, type_label, row


def _aggregate_oi_changes(
    daily_oi_digests: dict[str, dict],
    trading_days: list[date],
) -> dict[tuple[str, str, float, str], dict]:
    """Group strike-level rows by (product, expiry, strike, type) over the
    given trading days. Sum oi_change; track per-day deltas; capture
    last-seen at_close.

    Returns dict keyed by (product, expiry, strike, type) → {
        'delta_oi_sum':  int,
        'at_close':      int,        # from the last day that had this strike
        'last_seen_iso': str,
        'daily_deltas':  list[{'date': iso, 'delta_oi': int}],
    }
    """
    agg: dict[tuple[str, str, float, str], dict] = {}
    for iso, _digest, product, expiry, type_label, row in _iter_strike_rows(
        daily_oi_digests, trading_days
    ):
        key = (product, expiry, float(row["strike"]), type_label)
        bucket = agg.setdefault(key, {
            "delta_oi_sum":   0,
            "at_close":       0,
            "last_seen_iso":  iso,
            "daily_deltas":   [],
        })
        delta = int(row.get("oi_change", 0))
        bucket["delta_oi_sum"] += delta
        bucket["at_close"] = int(row.get("oi", 0))
        bucket["last_seen_iso"] = iso
        bucket["daily_deltas"].append({"date": iso, "delta_oi": delta})
    return agg


def _aggregate_volume(
    daily_oi_digests: dict[str, dict],
    trading_days: list[date],
) -> dict[tuple[str, str, float, str], dict]:
    """Same shape as _aggregate_oi_changes but for volume.

    Returns dict keyed by (product, expiry, strike, type) → {
        'volume_sum':     int,
        'daily_volumes':  list[{'date': iso, 'volume': int}],
    }
    """
    agg: dict[tuple[str, str, float, str], dict] = {}
    for iso, _digest, product, expiry, type_label, row in _iter_strike_rows(
        daily_oi_digests, trading_days
    ):
        key = (product, expiry, float(row["strike"]), type_label)
        bucket = agg.setdefault(key, {
            "volume_sum":     0,
            "daily_volumes":  [],
        })
        vol = int(row.get("volume", 0))
        bucket["volume_sum"] += vol
        bucket["daily_volumes"].append({"date": iso, "volume": vol})
    return agg


def _rank_oi_changes(
    agg: dict[tuple[str, str, float, str], dict],
    k: int,
) -> list[dict]:
    """Top-k by |delta_oi_sum|, ties broken by (product, expiry, strike, type).

    Excludes zero-delta entries. Returns a list of normalised dicts ready
    for the digest segment.
    """
    items = [(key, bucket) for key, bucket in agg.items() if bucket["delta_oi_sum"] != 0]
    # Sort: |delta| desc, then tie-breaker fields ascending.
    items.sort(key=lambda kb: (-abs(kb[1]["delta_oi_sum"]), kb[0]))
    top = items[:k]
    result = []
    for rank, (key, bucket) in enumerate(top, start=1):
        product, expiry, strike, type_label = key
        result.append({
            "rank":          rank,
            "product":       product,
            "expiry":        expiry,
            "strike":        strike,
            "type":          type_label,
            "delta_oi_sum":  bucket["delta_oi_sum"],
            "at_close":      bucket["at_close"],
            "daily_deltas":  bucket["daily_deltas"],
        })
    return result


def _rank_volume(
    agg: dict[tuple[str, str, float, str], dict],
    k: int,
) -> list[dict]:
    """Top-k by volume_sum, ties broken by (product, expiry, strike, type).

    Excludes zero-volume entries.
    """
    items = [(key, bucket) for key, bucket in agg.items() if bucket["volume_sum"] > 0]
    items.sort(key=lambda kb: (-kb[1]["volume_sum"], kb[0]))
    top = items[:k]
    result = []
    for rank, (key, bucket) in enumerate(top, start=1):
        product, expiry, strike, type_label = key
        result.append({
            "rank":           rank,
            "product":        product,
            "expiry":         expiry,
            "strike":         strike,
            "type":           type_label,
            "volume_sum":     bucket["volume_sum"],
            "daily_volumes":  bucket["daily_volumes"],
        })
    return result


# ---------------------------------------------------------------------------
# Flow / client filtering by date window
# ---------------------------------------------------------------------------

def _filter_rows_to_days(rows: list[dict], trading_days: list[date]) -> list[str]:
    """Keep rows whose `date` is in `trading_days`; return their raw_notes."""
    days = {d.isoformat() for d in trading_days}
    return [r["raw_note"] for r in rows if r.get("date") in days]


# ---------------------------------------------------------------------------
# Futures rollup
# ---------------------------------------------------------------------------

def _quarterly_in_scope(expiry: str) -> bool:
    return (
        len(expiry) == 2
        and expiry[0] in QUARTERLY_LETTERS
        and expiry[1] in QUARTERLY_YEARS
    )


def _futures_rollup(
    daily_oi_digests: dict[str, dict],
    trading_days: list[date],
) -> dict[str, dict]:
    """Per-quarterly week-level summary:
        oi_close:        last-seen 'oi' across the week
        oi_change_week:  sum of daily 'oi_change' across the week
        volume_week:     sum of daily 'volume' across the week
    """
    out: dict[str, dict] = {}
    last_iso: dict[str, str] = {}
    for d in trading_days:
        iso = d.isoformat()
        digest = daily_oi_digests.get(iso)
        if digest is None:
            continue
        for expiry, row in digest.get("futures", {}).items():
            if not _quarterly_in_scope(expiry):
                continue
            bucket = out.setdefault(expiry, {
                "oi_close":       0,
                "oi_change_week": 0,
                "volume_week":    0,
            })
            bucket["oi_change_week"] += int(row.get("oi_change", 0))
            bucket["volume_week"]    += int(row.get("volume", 0))
            # Capture latest at_close — depends on chronological iteration
            # (we iterate trading_days in order, so the last assignment wins).
            bucket["oi_close"] = int(row.get("oi", 0))
            last_iso[expiry] = iso
    return out


# ---------------------------------------------------------------------------
# Segment builder
# ---------------------------------------------------------------------------

def _build_segment(
    segment: Segment,
    *,
    daily_oi_digests: dict[str, dict],
    flow_rows: list[dict],
    client_rows: list[dict],
) -> dict:
    """One Segment → one digest entry."""
    days = segment.trading_days

    oi_agg = _aggregate_oi_changes(daily_oi_digests, days)
    vol_agg = _aggregate_volume(daily_oi_digests, days)

    out = {
        "name":               segment.name,
        "trading_days":       [d.isoformat() for d in days],
        "anchor_event_date":  None,
        "anchor_matcher":     None,
        "top_oi_changes":     _rank_oi_changes(oi_agg, TOP_K),
        "top_volume":         _rank_volume(vol_agg, TOP_K),
        "flow_notes":         _filter_rows_to_days(flow_rows, days),
        "client_trades":      _filter_rows_to_days(client_rows, days),
    }

    if segment.anchor_event is not None:
        out["anchor_event_date"] = segment.anchor_event.get("date")
        out["anchor_matcher"] = segment.anchor_event.get("matcher")

    return out


# ---------------------------------------------------------------------------
# Week summary
# ---------------------------------------------------------------------------

def _week_summary(
    daily_oi_digests: dict[str, dict],
    trading_days: list[date],
) -> dict:
    """Top builds (positive delta_oi_sum), top unwinds (negative), top volume,
    each capped at TOP_K, across the whole week.
    """
    oi_agg = _aggregate_oi_changes(daily_oi_digests, trading_days)
    vol_agg = _aggregate_volume(daily_oi_digests, trading_days)

    # Builds: positive delta only, sorted descending.
    builds_items = [(k, b) for k, b in oi_agg.items() if b["delta_oi_sum"] > 0]
    builds_items.sort(key=lambda kb: (-kb[1]["delta_oi_sum"], kb[0]))
    builds = []
    for rank, (key, bucket) in enumerate(builds_items[:TOP_K], start=1):
        product, expiry, strike, type_label = key
        builds.append({
            "rank":          rank,
            "product":       product,
            "expiry":        expiry,
            "strike":        strike,
            "type":          type_label,
            "delta_oi_sum":  bucket["delta_oi_sum"],
            "at_close":      bucket["at_close"],
        })

    # Unwinds: negative delta only, sorted ascending (most-negative first).
    unwinds_items = [(k, b) for k, b in oi_agg.items() if b["delta_oi_sum"] < 0]
    unwinds_items.sort(key=lambda kb: (kb[1]["delta_oi_sum"], kb[0]))
    unwinds = []
    for rank, (key, bucket) in enumerate(unwinds_items[:TOP_K], start=1):
        product, expiry, strike, type_label = key
        unwinds.append({
            "rank":          rank,
            "product":       product,
            "expiry":        expiry,
            "strike":        strike,
            "type":          type_label,
            "delta_oi_sum":  bucket["delta_oi_sum"],
            "at_close":      bucket["at_close"],
        })

    # Volume top-10 (no positive/negative split for volume).
    top_volume = _rank_volume(vol_agg, TOP_K)

    return {
        "top_oi_builds":   builds,
        "top_oi_unwinds":  unwinds,
        "top_volume":      top_volume,
    }


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

def _trading_days_of_week(monday: date) -> list[date]:
    """Mon-Fri starting at `monday`."""
    return [monday + timedelta(days=i) for i in range(5)]


def _detect_products(daily_oi_digests: dict[str, dict]) -> list[str]:
    """Union of all product keys seen in the week's daily digests."""
    products: set[str] = set()
    for d in daily_oi_digests.values():
        for p in d.get("options", {}):
            products.add(p)
    return sorted(products)


def build_digest(
    week_d: date,
    *,
    daily_oi_digests: dict[str, dict],
    flow_rows: list[dict] | None = None,
    client_rows: list[dict] | None = None,
    events: list[dict] | None = None,
    segments: list[Segment] | None = None,
    prior_weeks: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    """Compose the LLM-facing digest for the ISO week containing `week_d`.

    All inputs are pre-validated by upstream modules. Aggregator does no I/O
    and raises no exceptions in normal operation — partial data is tolerated
    via `warnings`.
    """
    monday, _ = week_window(week_d)
    week_days = _trading_days_of_week(monday)
    week_days_iso = [d.isoformat() for d in week_days]
    days_with_data = sorted(
        iso for iso in week_days_iso if iso in daily_oi_digests
    )

    flow_rows = flow_rows or []
    client_rows = client_rows or []
    events = events or []
    segments = segments or []
    prior_weeks = prior_weeks or []
    warnings = list(warnings or [])

    return {
        "week":                    week_label(week_d),
        "trading_days_in_week":    week_days_iso,
        "trading_days_with_data":  days_with_data,
        "products":                _detect_products(daily_oi_digests),
        "warnings":                warnings,

        "events":                  events,
        "futures_oi":              _futures_rollup(daily_oi_digests, week_days),

        "segments": [
            _build_segment(
                seg,
                daily_oi_digests=daily_oi_digests,
                flow_rows=flow_rows,
                client_rows=client_rows,
            )
            for seg in segments
        ],

        "week_summary":            _week_summary(daily_oi_digests, week_days),
        "prior_weeks":             prior_weeks,
    }
