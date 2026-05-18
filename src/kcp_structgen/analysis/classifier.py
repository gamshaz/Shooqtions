"""Surprise classification — deterministic Python, never the LLM.

Per spec §8: each matcher has an "inline" band. `|actual − estimate| <= band`
→ `'inline'`. Above the band → `'hot'` (actual exceeded consensus). Below the
band → `'cold'` (actual undershot consensus).

This is direction-agnostic numerically. The interpretation wrt price is up
to the LLM (system prompt tells it the mapping: hot CPI/NFP → bearish for
rates).

FOMC is the one event the LLM classifies itself — from a statement diff
produced by `fomc_scraper.py`. This module returns `None` for FOMC events.
"""

from __future__ import annotations

# Bands per spec §8. Same units as the underlying data (NFP is k of jobs,
# CPI is percentage points, etc.).
INLINE_BANDS: dict[str, float] = {
    # Jobs
    "NFP":          5_000,
    "ADP":          10_000,
    "JOBLESS":      5_000,
    "JOLTS":        100_000,
    # Prices (percentage points)
    "CPI":          0.1,
    "CORE_CPI":     0.1,
    "PCE":          0.1,
    "CORE_PCE":     0.1,
    "PPI":          0.1,
    "RETAIL_SALES": 0.1,
    # Growth (percentage points)
    "GDP":          0.1,
    # PMIs (points)
    "ISM_MFG":      0.3,
    "ISM_SVC":      0.3,
    "SP_PMI_MFG":   0.3,
    "SP_PMI_SVC":   0.3,
    # FOMC: deliberately absent; LLM classifies from statement diff
}


def classify_surprise(event: dict) -> str | None:
    """Return `'hot'` / `'cold'` / `'inline'`, or `None` if unclassifiable.

    Returns None when:
      - `matcher` is missing or unknown to this classifier (e.g. FOMC)
      - `actual` or `estimate` is missing / not numeric
    """
    matcher = event.get("matcher")
    if not matcher or matcher not in INLINE_BANDS:
        return None

    actual = event.get("actual")
    estimate = event.get("estimate")
    if actual is None or estimate is None:
        return None
    try:
        actual_f = float(actual)
        estimate_f = float(estimate)
    except (TypeError, ValueError):
        return None

    band = INLINE_BANDS[matcher]
    diff = actual_f - estimate_f
    # Tolerate IEEE-754 float jitter so a 2.9 vs 3.0 CPI (|diff|=0.10000000000000009)
    # still classifies as inline at band=0.1. The 1e-9 epsilon is comfortably
    # smaller than any meaningful surprise on any band in INLINE_BANDS.
    if abs(diff) <= band + 1e-9:
        return "inline"
    return "hot" if diff > 0 else "cold"


def classify_events(events: list[dict]) -> list[dict]:
    """In-place: set `surprise` on every event. Returns the same list."""
    for ev in events:
        ev["surprise"] = classify_surprise(ev)
    return events
