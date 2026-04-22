import pytest

from kcp_structgen.products import format_prodexp


def test_sofr_white():
    assert format_prodexp("SR3", "Z6") == "SFRZ6"


def test_sofr_midcurve():
    assert format_prodexp("0Q", "M7") == "0QM7"


def test_euribor():
    assert format_prodexp("ER", "U6") == "ERU6"


def test_euribor_midcurve():
    assert format_prodexp("0R", "Z6") == "0RZ6"


def test_sonia():
    assert format_prodexp("SFI", "H7") == "SFIH7"


def test_sonia_midcurve():
    assert format_prodexp("0N", "M7") == "0NM7"


def test_unknown_product():
    with pytest.raises(ValueError):
        format_prodexp("TY", "Z6")


def test_bad_expiry_month():
    with pytest.raises(ValueError):
        format_prodexp("SR3", "A6")  # A is not a valid futures month code at all


def test_monthly_expiry_accepted():
    """K (May) is a valid monthly; should not error."""
    assert format_prodexp("SR3", "K6") == "SFRK6"
    assert format_prodexp("ER", "V6") == "ERV6"


def test_bad_expiry_shape():
    with pytest.raises(ValueError):
        format_prodexp("SR3", "Z26")  # year must be single digit
