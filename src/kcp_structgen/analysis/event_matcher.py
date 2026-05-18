"""Match raw economic-event names to canonical matcher keys.

FMP and Bloomberg both emit events under slightly different strings across
periods ("Non Farm Payrolls" vs "Nonfarm Payrolls" vs "Change in Nonfarm
Payrolls"; "CPI YoY" vs "Consumer Price Index YoY" etc.). The matchers here
are regex-based, not exact-string, so the rest of the pipeline doesn't break
when FMP changes their naming.

Tier membership comes from spec §6. Unmatched events are ignored by the
segmenter (`matcher = None`) but their raw rows are preserved upstream so
the LLM still sees them in the digest appendix if needed.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

# Order matters here: more specific patterns must come before more general
# ones (e.g. CORE_CPI before CPI, CORE_PCE before PCE), because we walk the
# dict top-to-bottom and return the first match.
EVENT_MATCHERS: dict[str, str] = {
    # Tier 1
    "FOMC":         r"FOMC|Federal Funds|Fed Interest Rate|Rate Decision.*US",
    "CORE_CPI":     r"Core CPI|Core Consumer Price",
    "CPI":          r"Consumer Price Index|\bCPI\b(?!.*Core)",
    "CORE_PCE":     r"Core PCE|Core Personal Consumption",
    "PCE":          r"\bPCE\b(?!.*Core)|Personal Consumption",
    "NFP":          r"Non.?Farm.*Payroll|Nonfarm.*Payroll",
    "GDP":          r"\bGDP\b|Gross Domestic Product",
    "ISM_MFG":      r"ISM Manufacturing",
    "ISM_SVC":      r"ISM (Non-Manufacturing|Services)",
    # Tier 2
    "ADP":          r"ADP.*Employment|ADP.*Payroll",
    "SP_PMI_MFG":   r"S&P.*(Manufacturing.*PMI|PMI.*Manufacturing)",
    "SP_PMI_SVC":   r"S&P.*((Services|Composite).*PMI|PMI.*(Services|Composite))",
    "PPI":          r"Producer Price Index|\bPPI\b",
    "RETAIL_SALES": r"Retail Sales",
    "JOBLESS":      r"Initial Jobless|Continuing Jobless",
    "JOLTS":        r"JOLTS|Job Openings",
}

TIER_1: frozenset[str] = frozenset({
    "FOMC", "CPI", "CORE_CPI", "NFP", "PCE", "CORE_PCE",
    "GDP", "ISM_MFG", "ISM_SVC",
})

TIER_2: frozenset[str] = frozenset({
    "ADP", "PPI", "RETAIL_SALES", "JOBLESS", "JOLTS",
    "SP_PMI_MFG", "SP_PMI_SVC",
})

# Pre-compile once.
_COMPILED: list[tuple[str, re.Pattern]] = [
    (key, re.compile(pattern, re.IGNORECASE)) for key, pattern in EVENT_MATCHERS.items()
]


def match_event(event_name: str | None) -> str | None:
    """Return the matcher key (e.g. `'CPI'`) for this event name, or None if
    nothing matches. Case-insensitive."""
    if not event_name:
        return None
    for key, pattern in _COMPILED:
        if pattern.search(event_name):
            return key
    return None


def tier_of(matcher: str | None) -> str | None:
    """Return `'tier1'` / `'tier2'` / None for a matcher key."""
    if matcher is None:
        return None
    if matcher in TIER_1:
        return "tier1"
    if matcher in TIER_2:
        return "tier2"
    return None


def tag_events(events: list[dict]) -> list[dict]:
    """In-place: set the `matcher` field on every event using its
    `event_name`. Returns the same list for chaining.

    Idempotent: re-tagging events that already have a `matcher` is fine.
    """
    for ev in events:
        ev["matcher"] = match_event(ev.get("event_name"))
    return events


def dedupe_fomc(events: list[dict], window_hours: int = 6) -> list[dict]:
    """Collapse FOMC-flavoured rows clustered within `window_hours` into a
    single representative event per cluster.

    FMP emits multiple rows per FOMC meeting: statement release, rate
    decision, SEP (on quarterly meetings), press conference. They're useful
    individually for the LLM, but the segmenter treats one meeting as a
    single segment boundary — so we keep one representative row per cluster.

    The kept row is the first FOMC-tagged row in the cluster by `date`
    (lexicographic; ISO dates sort correctly).

    Non-FOMC rows pass through untouched. Input list order is preserved
    for non-FOMC rows; FOMC clusters collapse in place at the first
    occurrence's position.
    """
    if not events:
        return events
    # First, ensure every event has its matcher set so this works whether
    # or not the caller already called tag_events.
    for ev in events:
        if "matcher" not in ev:
            ev["matcher"] = match_event(ev.get("event_name"))

    fomc_indexed = [(i, ev) for i, ev in enumerate(events) if ev.get("matcher") == "FOMC"]
    if len(fomc_indexed) <= 1:
        return events

    def _parse_dt(ev: dict) -> datetime | None:
        d = ev.get("date")
        if not d:
            return None
        try:
            return datetime.fromisoformat(d)
        except ValueError:
            return None

    # Sort FOMC rows by parsed date (None last). Cluster within window_hours
    # of the cluster's first event.
    fomc_sorted = sorted(
        ((i, ev, _parse_dt(ev)) for i, ev in fomc_indexed),
        key=lambda t: (t[2] is None, t[2]),
    )

    keep_indices: set[int] = set()
    cluster_start: datetime | None = None
    cluster_kept_idx: int | None = None
    threshold = timedelta(hours=window_hours)

    for idx, ev, dt in fomc_sorted:
        if dt is None:
            # No date — keep on its own to avoid silently dropping
            keep_indices.add(idx)
            cluster_start = None
            cluster_kept_idx = None
            continue
        if cluster_start is None or (dt - cluster_start) > threshold:
            keep_indices.add(idx)
            cluster_start = dt
            cluster_kept_idx = idx
        # else: in same cluster; do NOT keep (collapses into cluster_kept_idx)

    return [
        ev for i, ev in enumerate(events)
        if ev.get("matcher") != "FOMC" or i in keep_indices
    ]
