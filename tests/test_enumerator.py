"""Unit tests for the structure enumerator.

These are the desk-convention regression wall. Concrete strike tuples come
from the user. If one fails after a change, confirm with the user before
updating the test.
"""

import pytest

from kcp_structgen.enumerator import (
    VARIANT_CAP,
    enumerate_structures,
    flies_broken_against,
    flies_broken_in_favour,
    flies_symmetric,
    groups_to_clipboard,
    groups_to_preview,
    outrights,
    verticals,
)


def _params(product="SR3", expiry="Z6", anchor=97.00, view="neutral",
            families=None, variants=None, flag=None, payout=None,
            expand_monthlies=False):
    return {
        "product": product,
        "expiry": expiry,
        "expand_monthlies": expand_monthlies,
        "anchor_price": anchor,
        "rate_events": None,
        "rate_delta_bp": None,
        "directional_view": view,
        "families": families,
        "variants": variants,
        "tightness": None,
        "cost_preference": None,
        "broken_direction_flag": flag,
        "max_payout_ticks": payout,
        "horizon_event": None,
    }


# ---------------------------------------------------------------------------
# Symmetric flies
# ---------------------------------------------------------------------------

def test_symmetric_call_fly_sfrz6_neutral():
    g = flies_symmetric(_params(view="neutral"))
    assert "SFRZ6 96.93/97.00/97.06 c fly" in g["lines"]
    assert "SFRZ6 96.87/97.00/97.12 c fly" in g["lines"]


def test_symmetric_put_fly_bearish():
    g = flies_symmetric(_params(view="bearish_price"))
    assert "SFRZ6 97.06/97.00/96.93 p fly" in g["lines"]
    assert "SFRZ6 97.12/97.00/96.87 p fly" in g["lines"]


def test_symmetric_fly_honors_max_payout():
    """max_payout_ticks=12.5 => only the 2-step fly (wings exactly 12.5bp)."""
    g = flies_symmetric(_params(view="bullish_price", payout=12.5))
    assert g["lines"] == ["SFRZ6 96.87/97.00/97.12 c fly"]


# ---------------------------------------------------------------------------
# Broken in-favour — desk-convention lock
# ---------------------------------------------------------------------------

def test_broken_in_favour_bullish_sfrz6_contains_user_tuples():
    """User-dictated tuples must appear (they are the ground truth)."""
    g = flies_broken_in_favour(_params(view="bullish_price"))
    assert "SFRZ6 96.87/97.00/97.06 c fly" in g["lines"]
    assert "SFRZ6 96.81/97.00/97.06 c fly" in g["lines"]
    assert "SFRZ6 96.81/97.00/97.12 c fly" in g["lines"]


def test_broken_in_favour_bearish_erz6_contains_user_tuple():
    g = flies_broken_in_favour(_params(product="ER", expiry="Z6", view="bearish_price"))
    assert "ERZ6 97.12/97.00/96.93 p fly" in g["lines"]


def test_broken_in_favour_bearish_erh7_user_tuple():
    g = flies_broken_in_favour(_params(product="ER", expiry="H7",
                                        anchor=97.875, view="bearish_price"))
    assert "ERH7 98.00/97.87/97.81 p fly" in g["lines"]


def test_broken_in_favour_neutral_is_empty():
    g = flies_broken_in_favour(_params(view="neutral"))
    assert g["lines"] == []


def test_broken_against_bullish_sfrz6_contains_user_tuples():
    g = flies_broken_against(_params(view="bullish_price"))
    assert "SFRZ6 96.93/97.00/97.12 c fly" in g["lines"]
    assert "SFRZ6 96.93/97.00/97.18 c fly" in g["lines"]
    assert "SFRZ6 96.87/97.00/97.18 c fly" in g["lines"]


def test_broken_against_widens_upper_for_bullish():
    g = flies_broken_against(_params(view="bullish_price"))
    for line in g["lines"]:
        strikes = [float(x) for x in line.split()[1].split("/")]
        assert (strikes[2] - strikes[1]) > (strikes[1] - strikes[0]), line


# ---------------------------------------------------------------------------
# Directional outrights
# ---------------------------------------------------------------------------

def test_outrights_bullish_no_puts():
    g = outrights(_params(view="bullish_price"))
    assert all(line.endswith(" c") for line in g["lines"])


def test_outrights_bullish_walks_down_from_anchor():
    """Calls at and BELOW target anchor (user feedback Feedback pt 2)."""
    g = outrights(_params(anchor=96.56, view="bullish_price"))
    # Anchor 96.56 snapped (grid 6.25bp): expect 96.56, 96.50, 96.43, 96.37, 96.31
    assert g["lines"][0] == "SFRZ6 96.56 c"  # at target
    assert "SFRZ6 96.50 c" in g["lines"]
    assert "SFRZ6 96.43 c" in g["lines"]
    # Must NOT contain strikes ABOVE target.
    assert not any(line.endswith(f" {k} c") for k in ("96.62", "96.68", "96.75") for line in g["lines"])


def test_outrights_bearish_no_calls():
    g = outrights(_params(view="bearish_price"))
    assert all(line.endswith(" p") for line in g["lines"])


def test_outrights_bearish_walks_up_from_anchor():
    """Puts at and ABOVE target anchor."""
    g = outrights(_params(anchor=97.43, view="bearish_price"))
    assert g["lines"][0] == "SFRZ6 97.43 p"
    assert "SFRZ6 97.50 p" in g["lines"]
    assert "SFRZ6 97.56 p" in g["lines"]
    # Must NOT contain strikes BELOW target.
    assert not any(line.endswith(f" {k} p") for k in ("97.37", "97.31") for line in g["lines"])


def test_outrights_neutral_has_both():
    g = outrights(_params(view="neutral"))
    assert any(line.endswith(" c") for line in g["lines"])
    assert any(line.endswith(" p") for line in g["lines"])


# ---------------------------------------------------------------------------
# Verticals
# ---------------------------------------------------------------------------

def test_verticals_bullish_emits_cs_only():
    g = verticals(_params(view="bullish_price"))
    assert all(" cs" in line for line in g["lines"])
    assert not any(" ps" in line for line in g["lines"])


def test_verticals_bullish_short_leg_at_anchor():
    """Bullish CS: short leg = anchor, long leg below. So K1 < K2 = anchor."""
    g = verticals(_params(anchor=96.56, view="bullish_price"))
    for line in g["lines"]:
        ks = [float(x) for x in line.split()[1].split("/")]
        assert ks[1] == 96.56, f"short leg should be at anchor in {line}"
        assert ks[0] < ks[1], f"long leg should be below anchor in {line}"


def test_verticals_bearish_emits_ps_only():
    g = verticals(_params(view="bearish_price"))
    assert all(" ps" in line for line in g["lines"])


def test_verticals_bearish_short_leg_at_anchor():
    """Bearish PS: short leg = anchor, long leg above."""
    g = verticals(_params(anchor=97.43, view="bearish_price"))
    for line in g["lines"]:
        ks = [float(x) for x in line.split()[1].split("/")]
        assert ks[1] == 97.43, f"short leg should be at anchor in {line}"
        assert ks[0] > ks[1], f"long leg should be above anchor in {line}"


def test_verticals_max_payout_one_step():
    """max_payout=6.25 => only 1-grid-step wide spreads. Strikes adjacent on grid."""
    g = verticals(_params(view="bullish_price", payout=6.25))
    for line in g["lines"]:
        # Strikes are written at 2dp, so 97.00/97.06 = one grid step on SR3.
        ks = [float(x) for x in line.split()[1].split("/")]
        assert abs(ks[1] - ks[0]) < 0.10, f"expected single-step spread, got {line}"


# ---------------------------------------------------------------------------
# Router, defaults, flag filtering
# ---------------------------------------------------------------------------

def test_default_families_when_none_includes_condor():
    groups = enumerate_structures(_params(view="bullish_price", families=None))
    headings = [g["heading"] for g in groups]
    assert any("Condor" in h for h in headings), (
        f"condors should be in the default set. Got: {headings}"
    )


def test_default_includes_broken_condors():
    """Feedback pt 2: user asked why no broken condors. They should appear."""
    groups = enumerate_structures(_params(view="bullish_price", families=None))
    headings = [g["heading"] for g in groups]
    assert any("Condors (broken, in favour)" in h for h in headings), headings
    assert any("Condors (broken, against)" in h for h in headings), headings


def test_broken_condor_bullish_in_favour_lower_wider():
    """Desk rule: bullish in-favour = lower outer wider than upper outer.
    Inspect internal tuples (not the truncated 2dp strings) for precision."""
    from kcp_structgen.enumerator import _condor_tuples_broken
    tuples = _condor_tuples_broken(_params(anchor=96.56, view="bullish_price"),
                                    "in_favour")
    assert tuples
    for k1, k2, k3, k4 in tuples:
        lower_outer = k2 - k1
        upper_outer = k4 - k3
        assert lower_outer > upper_outer + 1e-9, (
            f"expected lower outer > upper outer: {(k1, k2, k3, k4)}"
        )


def test_broken_condor_bullish_against_upper_wider():
    from kcp_structgen.enumerator import _condor_tuples_broken
    tuples = _condor_tuples_broken(_params(anchor=96.56, view="bullish_price"),
                                    "against")
    assert tuples
    for k1, k2, k3, k4 in tuples:
        lower_outer = k2 - k1
        upper_outer = k4 - k3
        assert upper_outer > lower_outer + 1e-9, (
            f"expected upper outer > lower outer: {(k1, k2, k3, k4)}"
        )


def test_broken_condor_flag_filters_groups():
    """broken_direction_flag=in_favour should drop the 'against' condor group."""
    groups = enumerate_structures(_params(view="bullish_price",
                                          families=["condor"], flag="in_favour"))
    headings = [g["heading"] for g in groups]
    assert not any("against" in h for h in headings), headings
    assert any("in favour" in h for h in headings), headings


# ---------------------------------------------------------------------------
# variants: precise narrowing (overrides families)
# ---------------------------------------------------------------------------

def test_variants_overrides_families_single():
    """variants=['fly_broken_in_favour'] → only that one group, no symmetric."""
    groups = enumerate_structures(_params(
        view="bullish_price",
        families=["fly", "condor"],  # should be ignored
        variants=["fly_broken_in_favour"],
    ))
    headings = [g["heading"] for g in groups]
    assert headings == ["Flies (broken, in favour)"], headings


def test_variants_broken_without_direction_emits_both():
    """User asked for 'broken flies' → in-favour AND against, no symmetric."""
    groups = enumerate_structures(_params(
        view="bullish_price",
        variants=["fly_broken_in_favour", "fly_broken_against"],
    ))
    headings = [g["heading"] for g in groups]
    assert "Flies (broken, in favour)" in headings
    assert "Flies (broken, against)" in headings
    assert "Flies (symmetric)" not in headings


def test_variants_symmetric_only():
    """'symmetric flies' → only symmetric group."""
    groups = enumerate_structures(_params(
        view="bullish_price", variants=["fly_symmetric"],
    ))
    headings = [g["heading"] for g in groups]
    assert headings == ["Flies (symmetric)"], headings


def test_variants_mixed_flies_and_condors():
    """'symmetric flies and broken condors in favour' → exactly those two."""
    groups = enumerate_structures(_params(
        view="bullish_price",
        variants=["fly_symmetric", "condor_broken_in_favour"],
    ))
    headings = [g["heading"] for g in groups]
    assert "Flies (symmetric)" in headings
    assert "Condors (broken, in favour)" in headings
    # The other variants should be dropped.
    assert "Flies (broken, in favour)" not in headings
    assert "Condors (symmetric)" not in headings


def test_variants_empty_list_falls_back_to_families():
    """variants=[] should NOT block families (treat as not narrowed)."""
    groups = enumerate_structures(_params(
        view="bullish_price", families=["fly"], variants=[],
    ))
    headings = [g["heading"] for g in groups]
    assert any("Flies" in h for h in headings)


def test_broken_flag_in_favour_drops_against_group():
    groups = enumerate_structures(_params(view="bullish_price", families=["fly"],
                                          flag="in_favour"))
    headings = [g["heading"] for g in groups]
    assert not any("against" in h for h in headings), headings


def test_broken_flag_against_drops_in_favour_group():
    groups = enumerate_structures(_params(view="bullish_price", families=["fly"],
                                          flag="against"))
    headings = [g["heading"] for g in groups]
    assert not any("in favour" in h for h in headings), headings


def test_families_filter_respects_scenario():
    groups = enumerate_structures(_params(families=["fly"], view="bullish_price"))
    for g in groups:
        assert "fl" in g["heading"].lower()


# ---------------------------------------------------------------------------
# Clipboard & preview
# ---------------------------------------------------------------------------

def test_clipboard_includes_headings_by_default():
    groups = enumerate_structures(_params(view="bullish_price",
                                          families=["outright", "vertical"]))
    clip = groups_to_clipboard(groups)
    assert "***" in clip  # headings wrapped with asterisks
    assert "Outright" in clip or "Vertical" in clip


def test_clipboard_without_headings_when_requested():
    groups = enumerate_structures(_params(view="bullish_price", families=["outright"]))
    clip = groups_to_clipboard(groups, include_headings=False)
    assert "***" not in clip


def test_preview_includes_headings_and_resolution_banner():
    p = _params(view="bullish_price", families=["outright"])
    groups = enumerate_structures(p)
    preview = groups_to_preview(groups, params=p)
    assert "***" in preview
    assert preview.startswith("Scenario resolved:")


def test_variant_cap_applied():
    all_fams = [
        "outright", "vertical", "fly", "condor", "ratio_spread",
        "ratio_fly", "rr", "straddle", "strangle", "calendar",
    ]
    groups = enumerate_structures(_params(view="bullish_price", families=all_fams))
    total = sum(len(g["lines"]) for g in groups)
    assert total <= VARIANT_CAP


# ---------------------------------------------------------------------------
# Monthlies expansion
# ---------------------------------------------------------------------------

def test_expand_monthlies_emits_vxz_for_z_quarterly():
    """expand_monthlies=True on Z6 should produce lines for V6, X6, and Z6."""
    p = _params(view="bullish_price", families=["outright"], expand_monthlies=True)
    groups = enumerate_structures(p)
    all_lines = " ".join(line for g in groups for line in g["lines"])
    assert "SFRV6" in all_lines
    assert "SFRX6" in all_lines
    assert "SFRZ6" in all_lines


def test_no_expand_by_default():
    p = _params(view="bullish_price", families=["outright"], expand_monthlies=False)
    groups = enumerate_structures(p)
    all_lines = " ".join(line for g in groups for line in g["lines"])
    assert "SFRV6" not in all_lines
    assert "SFRX6" not in all_lines
    assert "SFRZ6" in all_lines
