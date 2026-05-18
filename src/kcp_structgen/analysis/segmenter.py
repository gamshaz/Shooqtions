"""Week → list of segments for the aggregator.

A segment is a window of trading days plus an optional anchor event. The
aggregator rolls OI / flow / client-trade rows for those days into the
segment's slot in the structured digest the LLM sees.

Per spec §9 + §9.1:
  - Each tier-1 event in the week produces three segments:
    pre_<event>  = [prior trading day]
    event_day_<event> = [event date]
    post_<event> = [next trading day]
  - A week with no tier-1 events: one flat segment `week_flat` covering all
    Mon-Fri.
  - Two tier-1 events close together (e.g. CPI Wed + NFP Fri) emit segments
    around each event independently; trading days can appear in multiple
    segments (Thursday is both `post_CPI` and `pre_NFP`).
  - Event on Monday → pre is empty. Event on Friday → post is empty. Empty
    sub-segments still emit; the aggregator decides whether to render them
    in the digest.
  - Tier-2 events are ignored here (they're carried as "context" in the
    aggregator, not as segment boundaries).
  - Weekend events are ignored (no trading day).

US holiday handling is deferred to v2.1 (see v2_backlog.md). If Thursday is
a holiday, the day is still in the trading-day model here; the aggregator
will see no CME file for that day and handle the gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .event_matcher import tier_of
from .events_api import week_window


@dataclass
class Segment:
    """One window of the week handed to the aggregator.

    `trading_days` may be empty (e.g. pre-segment of a Monday event).
    `anchor_event` is None only for `week_flat` segments.
    """
    name: str
    trading_days: list[date]
    anchor_event: dict | None = field(default=None)


# Monday=0, ..., Friday=4. We treat the trading week as Mon-Fri.
_TRADING_WEEKDAYS = (0, 1, 2, 3, 4)


def _trading_days_of_week(monday: date) -> list[date]:
    """Return [Mon, Tue, Wed, Thu, Fri] starting at `monday`."""
    return [monday + timedelta(days=i) for i in range(5)]


def _event_trading_date(event: dict) -> date | None:
    """Parse the event's `date` field to a Python `date`. None if unparseable
    or the event falls on a Saturday/Sunday."""
    raw = event.get("date")
    if not raw:
        return None
    try:
        # `date` field is ISO-string per events_api normalisation.
        # Strip time-of-day if present.
        d = datetime.fromisoformat(str(raw).split("T", 1)[0].split(" ", 1)[0]).date()
    except (ValueError, TypeError):
        return None
    if d.weekday() not in _TRADING_WEEKDAYS:
        return None
    return d


def _prev_trading_day(d: date, week_days: list[date]) -> date | None:
    """Trading day immediately before `d` within the same Mon-Fri week.
    None if `d` is the Monday."""
    if d not in week_days:
        return None
    idx = week_days.index(d)
    if idx == 0:
        return None
    return week_days[idx - 1]


def _next_trading_day(d: date, week_days: list[date]) -> date | None:
    """Trading day immediately after `d` within the same Mon-Fri week.
    None if `d` is the Friday."""
    if d not in week_days:
        return None
    idx = week_days.index(d)
    if idx >= len(week_days) - 1:
        return None
    return week_days[idx + 1]


def segment_week(week_d: date, events: list[dict]) -> list[Segment]:
    """Build the segment list for the ISO week containing `week_d`.

    `events` should be tagged (`matcher`) and classified (`surprise`)
    already — typically the output of:
        events = load_events_for_week(week_d)
        tag_events(events)
        events = dedupe_fomc(events)
        classify_events(events)

    Only tier-1 events generate segments. If no tier-1 events fall in the
    Mon-Fri week, returns `[Segment(name='week_flat', trading_days=[Mon..Fri],
    anchor_event=None)]`.
    """
    monday, _friday = week_window(week_d)
    week_days = _trading_days_of_week(monday)

    # Filter to in-week, tier-1 events on a trading day.
    tier1_events: list[tuple[date, dict]] = []
    for ev in events:
        if tier_of(ev.get("matcher")) != "tier1":
            continue
        d = _event_trading_date(ev)
        if d is None:
            continue
        if d not in week_days:
            continue
        tier1_events.append((d, ev))

    if not tier1_events:
        return [Segment(name="week_flat",
                        trading_days=week_days,
                        anchor_event=None)]

    # Stable order: event-date ascending, then original list order for ties.
    tier1_events.sort(key=lambda t: t[0])

    segments: list[Segment] = []
    for event_date, ev in tier1_events:
        matcher = ev["matcher"]
        prev_d = _prev_trading_day(event_date, week_days)
        next_d = _next_trading_day(event_date, week_days)
        segments.append(Segment(
            name=f"pre_{matcher}",
            trading_days=[prev_d] if prev_d else [],
            anchor_event=ev,
        ))
        segments.append(Segment(
            name=f"event_day_{matcher}",
            trading_days=[event_date],
            anchor_event=ev,
        ))
        segments.append(Segment(
            name=f"post_{matcher}",
            trading_days=[next_d] if next_d else [],
            anchor_event=ev,
        ))
    return segments
