"""Structure enumerator.

Takes parsed params and emits grouped lists of PM trade-description strings.
All desk conventions live here as explicit code with unit tests.

Params schema (spec.md §2.2 + extensions):
    product: "SR3" | "0Q" | "ER" | "0R" | "SFI" | "0N"
    expiry:  "Z6" | "U6" | "V6" | "X6" | ...
    expand_monthlies: bool                # if True, emit for all 3 monthlies in the cycle
    anchor_price: float | None
    rate_events: [{"when": str, "delta_bp": num | [lo,hi]}] | None
    rate_delta_bp: num | [lo,hi] | None   # legacy single-event form
    directional_view: "bullish_price" | "bearish_price" | "neutral"
    families: list[str] | None
    tightness: "tight" | "medium" | "wide" | None
    cost_preference: "cheap" | "normal" | None
    broken_direction_flag: "in_favour" | "against" | None
    max_payout_ticks: float | None        # wing/spread width target
    horizon_event: str | None             # e.g. "fomc_sep" — placeholder for v2
"""

from __future__ import annotations

from typing import TypedDict

from .products import format_prodexp, monthly_expiries_for
from .rates import resolve_anchor_range, resolve_anchors
from .strikes import _grid_step, format_strike, snap_to_grid, walk

# Soft cap on total structures per run. User bumped to 150 after BoE scenario.
VARIANT_CAP = 150


class Group(TypedDict):
    heading: str
    lines: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class EnumeratorError(ValueError):
    pass


def _require(params: dict) -> None:
    missing = [k for k in ("product", "expiry") if params.get(k) is None]
    if missing:
        raise EnumeratorError(
            "scenario is missing required field(s): " + ", ".join(missing)
            + ". Please include product and expiry (e.g. Z6)."
        )
    if (params.get("anchor_price") is None
            and params.get("rate_delta_bp") is None
            and not params.get("rate_events")):
        raise EnumeratorError(
            "scenario gave no price level and no rate move. "
            "Say something like 'at 97.00' or '1 cut by Dec' or "
            "'hikes in Sep and small chance of cut in Dec'."
        )


def _pe(params: dict) -> str:
    return format_prodexp(params["product"], params["expiry"])


def _K(price: float, params: dict) -> str:
    return format_strike(price, params["product"])


def _anchor(params: dict) -> float:
    if params.get("anchor_price") is None:
        raise EnumeratorError("anchor_price is missing at emission time")
    return snap_to_grid(params["anchor_price"], params["product"])


def _is_bullish(params: dict) -> bool:
    """Use call structures? True when target anchor is above current price
    (price needs to rise). If current_price_override is set (multi-event
    dialog), that decides; otherwise fall back to directional_view."""
    cp = params.get("current_price_override")
    if cp is not None and params.get("anchor_price") is not None:
        return float(params["anchor_price"]) > float(cp)
    return params.get("directional_view") == "bullish_price"


def _is_bearish(params: dict) -> bool:
    """Use put structures? Mirror of _is_bullish."""
    cp = params.get("current_price_override")
    if cp is not None and params.get("anchor_price") is not None:
        return float(params["anchor_price"]) < float(cp)
    return params.get("directional_view") == "bearish_price"


def _payout_steps(params: dict) -> int | None:
    """Convert max_payout_ticks into grid steps for the current product.
    None if the parameter is not set."""
    payout = params.get("max_payout_ticks")
    if payout is None:
        return None
    step_bp = _grid_step(params["product"]) * 100  # 6.25 or 5.0
    steps = int(round(float(payout) / step_bp))
    return max(1, steps)


# ---------------------------------------------------------------------------
# Family builders — single-anchor, point-style
# ---------------------------------------------------------------------------

def outrights(params: dict) -> Group:
    """Strikes at and below target anchor, per user feedback (Feedback pt 2):
      - Bullish (cuts raise price, target > current): calls walking from
        anchor DOWN (anchor, anchor-1, anchor-2, ...). 'At or below target.'
      - Bearish (hikes lower price, target < current): puts walking from
        anchor UP (anchor, anchor+1, anchor+2, ...). Mirror of above.
      - Neutral: ATM call + ATM put + one step OTM each side.
    """
    pe = _pe(params)
    a = _anchor(params)
    step = _grid_step(params["product"])

    lines: list[str] = []
    if _is_bullish(params):
        for n in range(0, 5):  # anchor down to anchor-4 grid steps
            k = round(a - n * step, 10)
            lines.append(f"{pe} {_K(k, params)} c")
    elif _is_bearish(params):
        for n in range(0, 5):  # anchor up to anchor+4 grid steps
            k = round(a + n * step, 10)
            lines.append(f"{pe} {_K(k, params)} p")
    else:
        g = walk(a, params["product"], 1)
        k_m1, k_atm, k_p1 = g
        lines = [
            f"{pe} {_K(k_atm, params)} c",
            f"{pe} {_K(k_atm, params)} p",
            f"{pe} {_K(k_p1,  params)} c",
            f"{pe} {_K(k_m1,  params)} p",
        ]
    return {"heading": "Outrights", "lines": lines}


def verticals(params: dict) -> Group:
    """Vertical spreads at and below target anchor (per user feedback).

    Bullish (target > current, cuts): call spreads where the SHORT leg is at
    or below target anchor. Emitted as 'lower/higher cs' where higher=anchor.
    So `{anchor-N}/{anchor} cs` for N = 1..4. User ends up long the lower
    strike and short the anchor (the post-move level).

    Bearish (target < current, hikes): put spreads where the SHORT leg is at
    or above target anchor. `{anchor+N}/{anchor} ps`.

    Neutral: symmetric around anchor.

    If max_payout_ticks is set, only that spread width is emitted.
    """
    pe = _pe(params)
    a = _anchor(params)
    product = params["product"]
    step = _grid_step(product)

    steps_to_try = [_payout_steps(params)] if _payout_steps(params) else [1, 2, 3, 4]

    lines: list[str] = []
    for n in steps_to_try:
        k_below = round(a - n * step, 10)
        k_above = round(a + n * step, 10)
        if _is_bullish(params):
            # Long the lower (further from current), short the anchor.
            lines.append(f"{pe} {_K(k_below, params)}/{_K(a, params)} cs")
        elif _is_bearish(params):
            lines.append(f"{pe} {_K(k_above, params)}/{_K(a, params)} ps")
        else:
            lines.append(f"{pe} {_K(a, params)}/{_K(k_above, params)} cs")
            lines.append(f"{pe} {_K(a, params)}/{_K(k_below, params)} ps")
    return {"heading": "Vertical spreads", "lines": _dedupe(lines)}


def flies_symmetric(params: dict) -> Group:
    """Symmetric flies. Widths from max_payout_ticks if set, else 1/2/3 steps."""
    pe = _pe(params)
    a = _anchor(params)
    cp = "p" if _is_bearish(params) else "c"

    steps_to_try = [_payout_steps(params)] if _payout_steps(params) else [1, 2, 3]
    step = _grid_step(params["product"])

    lines: list[str] = []
    for n in steps_to_try:
        low = round(a - n * step, 10)
        up  = round(a + n * step, 10)
        if cp == "c":
            lines.append(f"{pe} {_K(low, params)}/{_K(a, params)}/{_K(up, params)} c fly")
        else:
            lines.append(f"{pe} {_K(up, params)}/{_K(a, params)}/{_K(low, params)} p fly")
    return {"heading": "Flies (symmetric)", "lines": _dedupe(lines)}


def flies_broken_in_favour(params: dict) -> Group:
    """Broken-wing flies, 'in favour' side.

    Bullish: lower wider than upper (wider-wing bp = max_payout if set).
    Bearish: upper wider than lower.
    """
    pe = _pe(params)
    a = _anchor(params)
    step = _grid_step(params["product"])

    # If max_payout set, the wider wing uses that width; narrower wing
    # enumerates 1..wider-1 grid steps.
    payout_steps = _payout_steps(params)
    if payout_steps:
        wider_steps_opts = [payout_steps]
    else:
        wider_steps_opts = [2, 3, 4]

    lines: list[str] = []
    for wider in wider_steps_opts:
        for narrow in range(1, wider):
            if _is_bullish(params):
                # lower wider, upper narrower
                low = round(a - wider  * step, 10)
                up  = round(a + narrow * step, 10)
                lines.append(f"{pe} {_K(low, params)}/{_K(a, params)}/{_K(up, params)} c fly")
            elif _is_bearish(params):
                # upper wider, lower narrower
                up  = round(a + wider  * step, 10)
                low = round(a - narrow * step, 10)
                lines.append(f"{pe} {_K(up, params)}/{_K(a, params)}/{_K(low, params)} p fly")
    return {"heading": "Flies (broken, in favour)", "lines": _dedupe(lines)}


def flies_broken_against(params: dict) -> Group:
    """Broken-wing flies, 'against' side. Mirror of in favour."""
    pe = _pe(params)
    a = _anchor(params)
    step = _grid_step(params["product"])

    payout_steps = _payout_steps(params)
    wider_steps_opts = [payout_steps] if payout_steps else [2, 3, 4]

    lines: list[str] = []
    for wider in wider_steps_opts:
        for narrow in range(1, wider):
            if _is_bullish(params):
                # upper wider, lower narrower (against)
                up  = round(a + wider  * step, 10)
                low = round(a - narrow * step, 10)
                lines.append(f"{pe} {_K(low, params)}/{_K(a, params)}/{_K(up, params)} c fly")
            elif _is_bearish(params):
                # lower wider, upper narrower (against)
                low = round(a - wider  * step, 10)
                up  = round(a + narrow * step, 10)
                lines.append(f"{pe} {_K(up, params)}/{_K(a, params)}/{_K(low, params)} p fly")
    return {"heading": "Flies (broken, against)", "lines": _dedupe(lines)}


def _condor_tuples_symmetric(params: dict) -> list[tuple[float, float, float, float]]:
    """Symmetric condors across anchor placements and outer-wing widths."""
    product = params["product"]
    step = _grid_step(product)
    a = _anchor(params)
    payout_steps = _payout_steps(params)
    outer_opts = [payout_steps] if payout_steps else [1, 2]
    inner_opts = [1, 2]

    tuples: list[tuple[float, float, float, float]] = []
    for inner in inner_opts:
        for outer in outer_opts:
            # Midpoint placement.
            k2 = round(a - 0.5 * inner * step, 10)
            k3 = round(a + 0.5 * inner * step, 10)
            tuples.append((round(k2 - outer * step, 10), k2, k3, round(k3 + outer * step, 10)))
            # Anchor = lower body.
            k2 = a
            k3 = round(a + inner * step, 10)
            tuples.append((round(k2 - outer * step, 10), k2, k3, round(k3 + outer * step, 10)))
            # Anchor = upper body.
            k3 = a
            k2 = round(a - inner * step, 10)
            tuples.append((round(k2 - outer * step, 10), k2, k3, round(k3 + outer * step, 10)))
    return tuples


def _condor_tuples_broken(params: dict, direction: str) -> list[tuple[float, float, float, float]]:
    """Broken condors with ON-GRID bodies.

    Body = two adjacent listed strikes. Two body placements enumerated:
      anchor-as-lower-body: K2 = anchor, K3 = anchor + 1 step.
      anchor-as-upper-body: K2 = anchor - 1 step, K3 = anchor.

    Outer wings asymmetric per direction rule (same as flies):
      Bullish, in favour: lower outer wider than upper (K2-K1 > K4-K3)
      Bullish, against:   upper outer wider than lower
      Bearish, in favour: upper outer wider than lower
      Bearish, against:   lower outer wider than upper
    """
    product = params["product"]
    step = _grid_step(product)
    a = _anchor(params)
    payout_steps = _payout_steps(params)
    wider_opts = [payout_steps] if payout_steps else [2, 3, 4]

    body_placements = [
        (a,                         round(a + step, 10)),  # anchor = lower body
        (round(a - step, 10),       a),                     # anchor = upper body
    ]

    tuples: list[tuple[float, float, float, float]] = []
    for k2, k3 in body_placements:
        for wider in wider_opts:
            for narrow in range(1, wider):
                wider_bp  = wider  * step
                narrow_bp = narrow * step
                if _is_bullish(params):
                    if direction == "in_favour":
                        k1 = round(k2 - wider_bp,  10)
                        k4 = round(k3 + narrow_bp, 10)
                    else:
                        k1 = round(k2 - narrow_bp, 10)
                        k4 = round(k3 + wider_bp,  10)
                elif _is_bearish(params):
                    if direction == "in_favour":
                        k1 = round(k2 - narrow_bp, 10)
                        k4 = round(k3 + wider_bp,  10)
                    else:
                        k1 = round(k2 - wider_bp,  10)
                        k4 = round(k3 + narrow_bp, 10)
                else:
                    continue
                tuples.append((k1, k2, k3, k4))
    return tuples


def _condor_lines_from_tuples(params: dict,
                              tuples: list[tuple[float, float, float, float]]
                              ) -> list[str]:
    pe = _pe(params)
    cp = "p" if _is_bearish(params) else "c"
    if cp == "c":
        return [f"{pe} {_K(x, params)}/{_K(y, params)}/{_K(z, params)}/{_K(w, params)} c condor"
                for x, y, z, w in tuples]
    return [f"{pe} {_K(w, params)}/{_K(z, params)}/{_K(y, params)}/{_K(x, params)} p condor"
            for x, y, z, w in tuples]


def condors_symmetric(params: dict) -> Group:
    """Symmetric and range-anchored condors."""
    product = params["product"]
    step = _grid_step(product)
    tuples: list[tuple[float, float, float, float]] = []

    # Range-anchored condor (probabilistic/multi-event scenarios).
    range_ = resolve_anchor_range(params)
    if range_ is not None:
        lo, hi = range_
        k2 = snap_to_grid(lo, product)
        if k2 > lo:
            k2 = round(k2 - step, 10)
        k3 = snap_to_grid(hi, product)
        if k3 < hi:
            k3 = round(k3 + step, 10)
        payout_steps = _payout_steps(params)
        outer_opts = [payout_steps] if payout_steps else [1, 2, 3]
        for n in outer_opts:
            k1 = round(k2 - n * step, 10)
            k4 = round(k3 + n * step, 10)
            tuples.append((k1, k2, k3, k4))

    tuples.extend(_condor_tuples_symmetric(params))
    return {"heading": "Condors (symmetric)",
            "lines": _dedupe(_condor_lines_from_tuples(params, tuples))}


def condors_broken_in_favour(params: dict) -> Group:
    tuples = _condor_tuples_broken(params, "in_favour")
    return {"heading": "Condors (broken, in favour)",
            "lines": _dedupe(_condor_lines_from_tuples(params, tuples))}


def condors_broken_against(params: dict) -> Group:
    tuples = _condor_tuples_broken(params, "against")
    return {"heading": "Condors (broken, against)",
            "lines": _dedupe(_condor_lines_from_tuples(params, tuples))}


def ratio_spreads(params: dict) -> Group:
    pe = _pe(params)
    a = _anchor(params)
    g = walk(a, params["product"], 3)
    _, k_m2, k_m1, _atm, k_p1, k_p2, _ = g
    lines: list[str] = []
    if _is_bearish(params):
        lines += [f"{pe} {_K(a, params)}/{_K(k_m1, params)} 1x2 ps",
                  f"{pe} {_K(a, params)}/{_K(k_m2, params)} 1x2 ps",
                  f"{pe} {_K(a, params)}/{_K(k_m1, params)} 1x3 ps"]
    elif _is_bullish(params):
        lines += [f"{pe} {_K(a, params)}/{_K(k_p1, params)} 1x2 cs",
                  f"{pe} {_K(a, params)}/{_K(k_p2, params)} 1x2 cs",
                  f"{pe} {_K(a, params)}/{_K(k_p1, params)} 1x3 cs"]
    else:
        lines += [f"{pe} {_K(a, params)}/{_K(k_p1, params)} 1x2 cs",
                  f"{pe} {_K(a, params)}/{_K(k_m1, params)} 1x2 ps"]
    return {"heading": "Ratio spreads", "lines": lines}


def ratio_flies(params: dict) -> Group:
    pe = _pe(params)
    a = _anchor(params)
    g = walk(a, params["product"], 2)
    _, k_m1, _atm, k_p1, _ = g
    cp = "p" if _is_bearish(params) else "c"
    if cp == "c":
        lines = [
            f"{pe} {_K(k_m1, params)}/{_K(a, params)}/{_K(k_p1, params)} 1x3x2 cfly",
            f"{pe} {_K(k_m1, params)}/{_K(a, params)}/{_K(k_p1, params)} 1x2.5x1 cfly",
            f"{pe} {_K(k_m1, params)}/{_K(a, params)}/{_K(k_p1, params)} 1x1.5x0.5 cfly",
        ]
    else:
        lines = [
            f"{pe} {_K(k_p1, params)}/{_K(a, params)}/{_K(k_m1, params)} 1x3x2 pfly",
            f"{pe} {_K(k_p1, params)}/{_K(a, params)}/{_K(k_m1, params)} 1x2.5x1 pfly",
            f"{pe} {_K(k_p1, params)}/{_K(a, params)}/{_K(k_m1, params)} 1x1.5x0.5 pfly",
        ]
    return {"heading": "Ratio flies", "lines": lines}


def risk_reversals(params: dict) -> Group:
    pe = _pe(params)
    g = walk(_anchor(params), params["product"], 3)
    _, k_m2, k_m1, _atm, k_p1, k_p2, _ = g
    lines = [
        f"{pe} {_K(k_m1, params)}/{_K(k_p1, params)} rr",
        f"{pe} {_K(k_m2, params)}/{_K(k_p2, params)} rr",
        f"{pe} {_K(k_m2, params)}/{_K(k_p1, params)} rr",
        f"{pe} {_K(k_m1, params)}/{_K(k_p2, params)} rr",
    ]
    return {"heading": "Risk reversals", "lines": _dedupe(lines)}


def straddles(params: dict) -> Group:
    return {"heading": "Straddles",
            "lines": [f"{_pe(params)} {_K(_anchor(params), params)} ^"]}


def strangles(params: dict) -> Group:
    pe = _pe(params)
    g = walk(_anchor(params), params["product"], 3)
    _, k_m2, k_m1, _atm, k_p1, k_p2, _ = g
    lines = [
        f"{pe} {_K(k_m1, params)}/{_K(k_p1, params)} strangle",
        f"{pe} {_K(k_m2, params)}/{_K(k_p2, params)} strangle",
    ]
    return {"heading": "Strangles", "lines": lines}


def calendars(params: dict) -> Group:
    from .products import MONTHLIES_FOR_QUARTERLY, QUARTERLY_MONTH_CODES
    pe_curr = _pe(params)
    month, year = params["expiry"][0], params["expiry"][1]
    # Find next quarterly after current expiry.
    qs = list(QUARTERLY_MONTH_CODES.values())
    # Snap to the quarterly: if current month is already quarterly, take next; else take the one containing this monthly.
    if month in QUARTERLY_MONTH_CODES.values():
        idx = qs.index(month)
        nxt_idx = idx + 1
    else:
        # Monthly — find the quarterly this monthly rolls into.
        for q, monthlies in MONTHLIES_FOR_QUARTERLY.items():
            if month in monthlies:
                nxt_idx = qs.index(q) + 1
                break
        else:
            nxt_idx = 0
    if nxt_idx >= len(qs):
        nxt_month = qs[0]
        nxt_year = str((int(year) + 1) % 10)
    else:
        nxt_month = qs[nxt_idx]
        nxt_year = year
    pe_next = format_prodexp(params["product"], f"{nxt_month}{nxt_year}")
    k = _K(_anchor(params), params)
    cp = "p" if _is_bearish(params) else "c"
    return {"heading": "Calendars",
            "lines": [f"{pe_curr} {k} {cp} vs {pe_next} {k} {cp}"]}


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

FAMILY_BUILDERS: dict[str, list] = {
    "outright":     [outrights],
    "vertical":     [verticals],
    "fly":          [flies_symmetric, flies_broken_in_favour, flies_broken_against],
    "condor":       [condors_symmetric, condors_broken_in_favour, condors_broken_against],
    "ratio_spread": [ratio_spreads],
    "ratio_fly":    [ratio_flies],
    "rr":           [risk_reversals],
    "straddle":     [straddles],
    "strangle":     [strangles],
    "calendar":     [calendars],
}

# Variant-level builder map. Used when params['variants'] is populated
# and caller wants only specific variants of flies/condors.
VARIANT_BUILDERS: dict[str, callable] = {
    "outright":               outrights,
    "vertical":               verticals,
    "fly_symmetric":          flies_symmetric,
    "fly_broken_in_favour":   flies_broken_in_favour,
    "fly_broken_against":     flies_broken_against,
    "condor_symmetric":       condors_symmetric,
    "condor_broken_in_favour": condors_broken_in_favour,
    "condor_broken_against":  condors_broken_against,
    "ratio_spread":           ratio_spreads,
    "ratio_fly":              ratio_flies,
    "rr":                     risk_reversals,
    "straddle":               straddles,
    "strangle":               strangles,
    "calendar":               calendars,
}

DEFAULT_FAMILIES: list[str] = ["outright", "vertical", "fly", "condor"]


def _filter_by_broken_flag(fam: str, params: dict) -> list:
    """Honour broken_direction_flag on flies + condors. Other families ignore."""
    builders = FAMILY_BUILDERS[fam]
    flag = params.get("broken_direction_flag")
    if fam == "fly":
        if flag == "in_favour":
            return [flies_symmetric, flies_broken_in_favour]
        if flag == "against":
            return [flies_symmetric, flies_broken_against]
    elif fam == "condor":
        if flag == "in_favour":
            return [condors_symmetric, condors_broken_in_favour]
        if flag == "against":
            return [condors_symmetric, condors_broken_against]
    return builders


def _enumerate_one_anchor_one_expiry(params: dict) -> list[Group]:
    """Route params to builders.

    Precedence:
      1. params['variants']: precise list of variant keys (overrides families
         entirely). Used when the user narrowed their scenario, e.g.
         'show symmetric flies and broken condors in favour'.
      2. params['families']: broad family names; emits every variant builder.
      3. Neither set: DEFAULT_FAMILIES.
    """
    variants = params.get("variants")
    if variants:
        groups: list[Group] = []
        for v in variants:
            build = VARIANT_BUILDERS.get(v)
            if build is None:
                continue
            g = build(params)
            if g["lines"]:
                groups.append(g)
        return groups

    families = params.get("families")
    default_used = families is None
    if default_used:
        families = DEFAULT_FAMILIES

    groups = []
    for fam in families:
        builders = _filter_by_broken_flag(fam, params)
        if not builders:
            continue
        for build in builders:
            g = build(params)
            if g["lines"]:
                groups.append(g)

    if default_used and groups:
        groups[0] = {
            "heading": groups[0]["heading"] + "  (default set — scenario did not specify families)",
            "lines":   groups[0]["lines"],
        }
    return groups


def _merge_groups(all_groups: list[list[Group]]) -> list[Group]:
    """Merge multiple per-anchor/per-expiry runs by heading, deduping lines."""
    merged: dict[str, list[str]] = {}
    order: list[str] = []
    for groups in all_groups:
        for g in groups:
            if g["heading"] not in merged:
                merged[g["heading"]] = []
                order.append(g["heading"])
            merged[g["heading"]].extend(g["lines"])
    return [{"heading": h, "lines": _dedupe(merged[h])} for h in order]


def enumerate_structures(params: dict) -> list[Group]:
    """Route params to family builders across all anchors and (if requested)
    all 3 monthlies in the quarterly cycle."""
    _require(params)

    # Decide which expiries to emit for.
    if params.get("expand_monthlies"):
        expiries = monthly_expiries_for(params["expiry"])
    else:
        expiries = [params["expiry"]]

    # Resolve anchors from rate_events / rate_delta_bp / anchor_price.
    anchors = resolve_anchors(params)
    if not anchors and params.get("anchor_price") is not None:
        anchors = [float(params["anchor_price"])]
    if not anchors:
        raise EnumeratorError("could not resolve any anchor price from scenario")

    all_runs: list[list[Group]] = []
    for expiry in expiries:
        for a in anchors:
            sub = dict(params)
            sub["expiry"] = expiry
            sub["anchor_price"] = a
            sub["expand_monthlies"] = False  # prevent recursion
            all_runs.append(_enumerate_one_anchor_one_expiry(sub))

    groups = _merge_groups(all_runs)
    return _apply_variant_cap(groups)


def _apply_variant_cap(groups: list[Group]) -> list[Group]:
    total = sum(len(g["lines"]) for g in groups)
    if total <= VARIANT_CAP:
        return groups
    kept: list[Group] = []
    remaining = VARIANT_CAP
    for g in groups:
        if remaining <= 0:
            break
        if len(g["lines"]) <= remaining:
            kept.append(g)
            remaining -= len(g["lines"])
        else:
            kept.append({"heading": g["heading"] + "  (truncated)",
                         "lines":   g["lines"][:remaining]})
            remaining = 0
    return kept


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _resolution_banner(params: dict) -> str:
    """Top-of-preview line showing what the tool actually resolved.
    Helps the user spot a mis-parse before they paste into PM."""
    bits: list[str] = []
    bits.append(f"product={params['product']} expiry={params['expiry']}")
    if params.get("anchor_price") is not None:
        bits.append(f"anchor={params['anchor_price']}")
    elif params.get("rate_events"):
        bits.append(f"rate_events={params['rate_events']}")
    elif params.get("rate_delta_bp") is not None:
        bits.append(f"rate_delta_bp={params['rate_delta_bp']}")
    if params.get("directional_view"):
        bits.append(f"view={params['directional_view']}")
    if params.get("broken_direction_flag"):
        bits.append(f"broken={params['broken_direction_flag']}")
    if params.get("max_payout_ticks") is not None:
        bits.append(f"max_payout={params['max_payout_ticks']} ticks")
    if params.get("expand_monthlies"):
        bits.append("monthlies=all")
    return "Scenario resolved: " + "  ".join(bits)


def groups_to_clipboard(groups: list[Group], *, include_headings: bool = True) -> str:
    """Clipboard payload. Headings on by default per user request (Layer 1 testing).
    PM will redden heading lines — that's intentional; user deletes or PM ignores."""
    out: list[str] = []
    for g in groups:
        if include_headings:
            out.append(f"***{g['heading']}***")
        out.extend(g["lines"])
        if include_headings:
            out.append("")
    return "\n".join(out).rstrip()


def groups_to_preview(groups: list[Group], params: dict | None = None) -> str:
    blocks: list[str] = []
    if params is not None:
        blocks.append(_resolution_banner(params))
        blocks.append("")
    for g in groups:
        blocks.append(f"***{g['heading']}***")
        blocks.extend(g["lines"])
        blocks.append("")
    return "\n".join(blocks).rstrip()
