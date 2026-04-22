import pytest

from kcp_structgen.strikes import format_strike, snap_to_grid, walk


# ---------------------------------------------------------------------------
# Snapping
# ---------------------------------------------------------------------------

def test_sofr_snap_on_grid():
    assert snap_to_grid(97.0625, "SR3") == pytest.approx(97.0625)


def test_sofr_snap_off_grid_rounds_nearest():
    # 97.07 is closer to 97.0625 than 97.125
    assert snap_to_grid(97.07, "SR3") == pytest.approx(97.0625)


def test_sonia_snap_on_grid():
    assert snap_to_grid(96.25, "SFI") == pytest.approx(96.25)


def test_sonia_snap_off_grid():
    assert snap_to_grid(96.27, "SFI") == pytest.approx(96.25)


# ---------------------------------------------------------------------------
# Formatting: 2-decimal truncation for all products (desk rule)
# ---------------------------------------------------------------------------

def test_sofr_format_truncates_to_2dp():
    assert format_strike(97.0625, "SR3") == "97.06"
    assert format_strike(96.9375, "SR3") == "96.93"
    assert format_strike(97.1875, "SR3") == "97.18"
    assert format_strike(96.8125, "SR3") == "96.81"


def test_euribor_format_truncates_to_2dp():
    assert format_strike(97.0625, "ER") == "97.06"
    assert format_strike(96.8125, "ER") == "96.81"


def test_sonia_format_native_2dp():
    assert format_strike(96.25, "SFI") == "96.25"
    assert format_strike(96.30, "SFI") == "96.30"


def test_exact_2dp_price_survives():
    # An already-2dp strike should round-trip cleanly.
    assert format_strike(97.00, "SR3") == "97.00"


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------

def test_walk_sofr_one_step():
    result = walk(97.00, "SR3", 1)
    assert len(result) == 3
    assert result[0] == pytest.approx(96.9375)
    assert result[1] == pytest.approx(97.00)
    assert result[2] == pytest.approx(97.0625)


def test_walk_sonia_two_steps():
    result = walk(96.25, "SFI", 2)
    assert len(result) == 5
    assert result == pytest.approx([96.15, 96.20, 96.25, 96.30, 96.35])


def test_walk_snaps_offgrid_anchor():
    # 97.07 snaps to 97.0625; the walk is centred there.
    result = walk(97.07, "SR3", 1)
    assert result[1] == pytest.approx(97.0625)


def test_walk_formatted_sofr():
    grid = walk(97.00, "SR3", 2)
    formatted = [format_strike(k, "SR3") for k in grid]
    assert formatted == ["96.87", "96.93", "97.00", "97.06", "97.12"]
