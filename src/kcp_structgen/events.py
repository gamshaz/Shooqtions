"""Central-bank and macro event calendar.

v1: hardcoded table of 2026 dates the desk cares about. Maintained manually
as dates change. When we plug into blpapi later, this module's public
surface (`next_event`, `event_date`) stays the same and the implementation
swaps underneath.

v2 TODO — event/expiry coverage mapping. Currently this module lists event
dates, but nothing knows whether a given option expiry (e.g. Z6) actually
covers a given event (e.g. FOMC Dec). Adding this requires a table of
listed expiry dates per contract (CME/ICE websites, holiday-adjusted) and
a simple `expiry_covers_event(expiry, event) -> bool` lookup. Deferred per
user on 2026-04-22. See conversation history for full spec.

Dates are 'best available as of project start (2026-04-21)'. Update as the
schedule firms up.
"""

from __future__ import annotations

from datetime import date

# Event name -> list of (ISO date, human label) tuples, sorted ascending.
# Keep names lowercase for case-insensitive matching.
EVENTS_2026: dict[str, list[tuple[str, str]]] = {
    "fomc":  [("2026-01-28", "FOMC Jan"),
              ("2026-03-18", "FOMC Mar"),
              ("2026-04-29", "FOMC Apr/May"),
              ("2026-06-17", "FOMC Jun"),
              ("2026-07-29", "FOMC Jul"),
              ("2026-09-16", "FOMC Sep"),
              ("2026-11-04", "FOMC Nov"),
              ("2026-12-16", "FOMC Dec")],
    "ecb":   [("2026-01-22", "ECB Jan"),
              ("2026-03-12", "ECB Mar"),
              ("2026-04-16", "ECB Apr"),
              ("2026-06-04", "ECB Jun"),
              ("2026-07-23", "ECB Jul"),
              ("2026-09-10", "ECB Sep"),
              ("2026-10-29", "ECB Oct"),
              ("2026-12-17", "ECB Dec")],
    "boe":   [("2026-02-05", "BoE Feb"),
              ("2026-03-19", "BoE Mar"),
              ("2026-05-07", "BoE May"),
              ("2026-06-18", "BoE Jun"),
              ("2026-08-06", "BoE Aug"),
              ("2026-09-17", "BoE Sep"),
              ("2026-11-05", "BoE Nov"),
              ("2026-12-17", "BoE Dec")],
    "nfp":   [("2026-01-09", "NFP Jan"),
              ("2026-02-06", "NFP Feb"),
              ("2026-03-06", "NFP Mar"),
              ("2026-04-03", "NFP Apr"),
              ("2026-05-01", "NFP May"),
              ("2026-06-05", "NFP Jun"),
              ("2026-07-02", "NFP Jul"),
              ("2026-08-07", "NFP Aug"),
              ("2026-09-04", "NFP Sep"),
              ("2026-10-02", "NFP Oct"),
              ("2026-11-06", "NFP Nov"),
              ("2026-12-04", "NFP Dec")],
}


def next_event_after(name: str, ref: date) -> tuple[date, str] | None:
    """Return the first scheduled event of `name` strictly after `ref`.

    Returns (date, label) or None if nothing is scheduled.
    """
    events = EVENTS_2026.get(name.lower(), [])
    for iso, label in events:
        d = date.fromisoformat(iso)
        if d > ref:
            return d, label
    return None


def event_date(name: str, month: int, year: int) -> date | None:
    """Return the date of the given event in the given month+year, if any."""
    events = EVENTS_2026.get(name.lower(), [])
    for iso, _label in events:
        d = date.fromisoformat(iso)
        if d.month == month and (d.year % 100) == (year % 100):
            return d
    return None
