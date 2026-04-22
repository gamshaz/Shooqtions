"""Current-rate lookup and anchor resolution.

Reads the desk's current reference rates from `current_rates.json` at repo root.
Resolves a parsed scenario's anchor price from one of three sources, in order:

1. Explicit `anchor_price` in params (user said a number) → use as-is.
2. `rate_delta_bp` in params (user said 'N cuts/hikes/chance') → compute
   anchor from current rate + delta. Price = 100 - rate, so a cut (negative
   rate_delta) becomes a positive price move.
3. Neither → raise EnumeratorError.

Probabilistic language ('some chance of a cut', 'likely hike') is expanded
into MULTIPLE anchors covering the probability range, so the enumerator
emits a broader menu (user's 'give them everything' philosophy).

Data source is pluggable: `load_current_rates()` today reads a local JSON,
but the signature is stable so we can swap to a PM pull or other feed later.
"""

from __future__ import annotations

import json
from pathlib import Path

# Path walks up: src/kcp_structgen/rates.py -> src -> repo root
RATES_FILE = Path(__file__).resolve().parents[2] / "current_rates.json"


class RatesError(ValueError):
    """Rates file missing, unreadable, or product not listed."""


def load_current_rates() -> dict[str, float]:
    """Load the desk's current reference rates from local JSON.

    Returns a dict of product code (e.g. 'SR3') -> current price (e.g. 96.31).
    """
    if not RATES_FILE.is_file():
        raise RatesError(
            f"current_rates.json not found at {RATES_FILE}. "
            "Create it at the repo root with a dict of product -> current price."
        )
    try:
        data = json.loads(RATES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RatesError(f"current_rates.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RatesError("current_rates.json must be a JSON object")
    return {k: float(v) for k, v in data.items()}


def _current_price(product: str) -> float:
    rates = load_current_rates()
    if product not in rates:
        raise RatesError(
            f"product {product!r} not in current_rates.json "
            f"(have: {sorted(rates)})"
        )
    return rates[product]


def _collapse_rate_events(events: list[dict]) -> float | tuple[float, float]:
    """Sum a list of rate_events into a single net delta.

    Each event is {"when": ..., "delta_bp": number | [lo, hi]}.
    Fixed deltas sum to a number. Ranges propagate: if any event has a range,
    the result is the sum of all fixed deltas plus the [sum_lo, sum_hi] of
    the range-events' ends.
    """
    fixed_sum = 0.0
    lo_sum = 0.0
    hi_sum = 0.0
    has_range = False
    for ev in events:
        d = ev.get("delta_bp")
        if isinstance(d, (int, float)):
            fixed_sum += float(d)
            lo_sum += float(d)
            hi_sum += float(d)
        elif isinstance(d, (list, tuple)) and len(d) == 2:
            a, b = float(d[0]), float(d[1])
            lo_sum += min(a, b)
            hi_sum += max(a, b)
            has_range = True
        else:
            raise RatesError(f"rate_events entry has bad delta_bp: {ev!r}")
    if has_range:
        return (lo_sum, hi_sum)
    return fixed_sum


def _anchors_from_delta(current: float, delta) -> list[float]:
    """Convert a net rate delta (bp) into one or three anchor prices.

    Price = 100 - rate, so rate_delta is subtracted (cut = negative delta =
    positive price move).
    """
    if isinstance(delta, (int, float)):
        return [round(current - float(delta) / 100.0, 10)]
    if isinstance(delta, (list, tuple)) and len(delta) == 2:
        lo, hi = float(delta[0]), float(delta[1])
        mid = (lo + hi) / 2.0
        return sorted({
            round(current - lo  / 100.0, 10),
            round(current - mid / 100.0, 10),
            round(current - hi  / 100.0, 10),
        })
    raise RatesError(f"delta has unexpected shape: {delta!r}")


def resolve_anchor_range(params: dict) -> tuple[float, float] | None:
    """Return (lo_price, hi_price) if the scenario implies a terminal range,
    else None. Used by range-aware families (condor, vertical, etc.).

    A range is present when:
    - rate_events contains a probabilistic event (list delta), OR
    - explicit [anchor_lo, anchor_hi] is given (not yet supported).
    """
    events = params.get("rate_events")
    if events:
        net = _collapse_rate_events(events)
        if isinstance(net, tuple):
            current = _current_price(params["product"])
            a1 = round(current - net[0] / 100.0, 10)
            a2 = round(current - net[1] / 100.0, 10)
            return (min(a1, a2), max(a1, a2))
    # Legacy single-field rate_delta_bp as a range.
    delta = params.get("rate_delta_bp")
    if isinstance(delta, (list, tuple)) and len(delta) == 2:
        current = _current_price(params["product"])
        a1 = round(current - float(delta[0]) / 100.0, 10)
        a2 = round(current - float(delta[1]) / 100.0, 10)
        return (min(a1, a2), max(a1, a2))
    return None


def resolve_anchors(params: dict) -> list[float]:
    """Return one or more anchor prices for the given parsed params.

    Precedence:
    1. Explicit anchor_price → [anchor_price]
    2. rate_events (list of sequential rate moves) → collapse to net delta,
       then single or three anchors depending on whether net is a range.
    3. Legacy rate_delta_bp (number or [lo,hi]) → single or three anchors.
    4. Nothing → [].
    """
    if params.get("anchor_price") is not None:
        return [float(params["anchor_price"])]

    events = params.get("rate_events")
    if events:
        net = _collapse_rate_events(events)
        current = _current_price(params["product"])
        return _anchors_from_delta(current, net)

    delta = params.get("rate_delta_bp")
    if delta is None:
        return []

    current = _current_price(params["product"])
    return _anchors_from_delta(current, delta)
