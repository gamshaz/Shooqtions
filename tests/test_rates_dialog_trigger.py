"""Tests for the 'needs current price' dialog trigger logic."""

from kcp_structgen.rates import scenario_needs_current_price


def _p(**kw):
    base = {"product": "ER", "expiry": "Z6", "anchor_price": None,
            "rate_events": None, "rate_delta_bp": None,
            "current_price_override": None}
    base.update(kw)
    return base


def test_no_ask_when_explicit_anchor():
    assert not scenario_needs_current_price(_p(anchor_price=97.50))


def test_no_ask_for_single_event():
    assert not scenario_needs_current_price(
        _p(rate_events=[{"when": "2026-09", "delta_bp": 25}])
    )


def test_no_ask_for_bare_rate_delta():
    assert not scenario_needs_current_price(_p(rate_delta_bp=-25))


def test_ask_for_multi_event_scenario():
    assert scenario_needs_current_price(_p(rate_events=[
        {"when": "2026-09", "delta_bp": 25},
        {"when": "2026-12", "delta_bp": [-3.75, -10.0]},
    ]))


def test_no_ask_after_override_set():
    assert not scenario_needs_current_price(_p(
        rate_events=[
            {"when": "2026-09", "delta_bp": 25},
            {"when": "2026-12", "delta_bp": -25},
        ],
        current_price_override=97.36,
    ))
