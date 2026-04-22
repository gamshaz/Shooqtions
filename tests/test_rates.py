"""Tests for the rates module and end-to-end anchor resolution."""

import pytest

from kcp_structgen.enumerator import enumerate_structures
from kcp_structgen.rates import load_current_rates, resolve_anchors


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def test_loader_has_all_v1_products():
    rates = load_current_rates()
    for p in ("SR3", "0Q", "ER", "0R", "SFI", "0N"):
        assert p in rates, f"{p} missing from current_rates.json"


def test_loader_values_in_price_range():
    # All v1 products should be priced in the 95-100 range. If we see
    # something outside this, it's probably been entered as a rate by mistake.
    for p, v in load_current_rates().items():
        assert 90.0 <= v <= 100.0, f"{p} = {v} looks wrong (rate not price?)"


# ---------------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------------

def _base(product="SR3"):
    return {"product": product, "expiry": "Z6", "expand_monthlies": False,
            "anchor_price": None, "rate_events": None, "rate_delta_bp": None,
            "directional_view": "neutral", "families": None, "tightness": None,
            "cost_preference": None, "broken_direction_flag": None,
            "max_payout_ticks": None, "horizon_event": None}


def test_anchor_explicit_price_wins():
    p = _base()
    p["anchor_price"] = 97.00
    p["rate_delta_bp"] = -50  # should be ignored
    assert resolve_anchors(p) == [97.00]


def test_anchor_from_cut_sofr():
    """1 cut = -25bp rate -> +25bp price. Current SOFR 96.31 -> 96.56."""
    p = _base("SR3")
    p["rate_delta_bp"] = -25
    assert resolve_anchors(p) == [pytest.approx(96.56)]


def test_anchor_from_hike_euribor():
    """1 hike = +25bp rate -> -25bp price. Current Euribor 97.93 -> 97.68."""
    p = _base("ER")
    p["rate_delta_bp"] = 25
    assert resolve_anchors(p) == [pytest.approx(97.68)]


def test_anchor_from_half_cut_sonia():
    p = _base("SFI")
    p["rate_delta_bp"] = -12.5
    # SONIA current 96.25 -> 96.25 + 0.125 = 96.375
    assert resolve_anchors(p) == [pytest.approx(96.375)]


def test_anchor_from_probabilistic_cut():
    """'Some chance of a cut' -> three anchors at lo/mid/hi."""
    p = _base("SR3")
    p["rate_delta_bp"] = [-3.75, -10.0]
    anchors = resolve_anchors(p)
    assert len(anchors) == 3
    # Current SOFR 96.31. lo=-3.75 bp move -> 96.31 + 0.0375 = 96.3475
    #                     hi=-10  bp move -> 96.31 + 0.10   = 96.41
    #                     mid=-6.875      -> 96.31 + 0.06875 = 96.37875
    assert anchors[0] == pytest.approx(96.3475)
    assert anchors[1] == pytest.approx(96.37875)
    assert anchors[2] == pytest.approx(96.41)


def test_no_anchor_no_delta_returns_empty():
    assert resolve_anchors(_base()) == []


# ---------------------------------------------------------------------------
# End-to-end: enumerator consumes rate_delta_bp
# ---------------------------------------------------------------------------

def test_enumerator_1_cut_dec_sofr():
    """'1 cut by december in sofr' -> anchor ~96.56; produces SFRZ6 structures."""
    p = _base("SR3")
    p["rate_delta_bp"] = -25
    p["directional_view"] = "bullish_price"
    p["families"] = ["fly"]
    groups = enumerate_structures(p)
    assert groups
    all_lines = [line for g in groups for line in g["lines"]]
    # 96.56 is on the 6.25bp grid. Symmetric fly with 1-step wings should
    # be 96.50/96.56/96.62 -> '96.50/96.56/96.62' written at 2dp.
    assert any("SFRZ6 96.50/96.56/96.62 c fly" in line for line in all_lines), (
        f"expected 96.50/96.56/96.62 symmetric call fly in: {all_lines}"
    )


def test_enumerator_probabilistic_produces_multiple_anchors():
    """Probabilistic delta should produce strictly more structures than a single delta."""
    p_range = _base("SR3")
    p_range["rate_delta_bp"] = [-3.75, -10.0]
    p_range["directional_view"] = "bullish_price"
    p_range["families"] = ["fly"]

    p_single = dict(p_range)
    p_single["rate_delta_bp"] = -6.875  # the mid

    lines_range = [line for g in enumerate_structures(p_range) for line in g["lines"]]
    lines_single = [line for g in enumerate_structures(p_single) for line in g["lines"]]

    assert len(lines_range) > len(lines_single), (
        "probabilistic rate_delta_bp should yield more structures than a single delta"
    )


def test_enumerator_requires_anchor_or_delta():
    from kcp_structgen.enumerator import EnumeratorError
    with pytest.raises(EnumeratorError):
        enumerate_structures(_base())
