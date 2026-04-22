"""Product codes, month codes, expiry formatting.

Produces the `{PRODEXP}` prefix used by every PM trade description
(e.g. `SFRZ6`, `ERU6`, `0QM7`). See docs/pm_grammar.md §v1 scope.
"""

from __future__ import annotations

PRODUCT_TICKERS: dict[str, str] = {
    "SR3": "SFR",
    "0Q":  "0Q",
    "ER":  "ER",
    "0R":  "0R",
    "SFI": "SFI",
    "0N":  "0N",
}

VALID_PRODUCTS: frozenset[str] = frozenset(PRODUCT_TICKERS)

QUARTERLY_MONTH_CODES: dict[int, str] = {3: "H", 6: "M", 9: "U", 12: "Z"}
ALL_MONTH_CODES: dict[int, str] = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
VALID_MONTH_CODES: frozenset[str] = frozenset(ALL_MONTH_CODES.values())
VALID_QUARTERLY_CODES: frozenset[str] = frozenset(QUARTERLY_MONTH_CODES.values())


# The 3 monthly expiries rolling into each quarterly (last monthly == quarterly).
# All three share the same underlying future (the quarterly).
MONTHLIES_FOR_QUARTERLY: dict[str, list[str]] = {
    "H": ["F", "G", "H"],  # Mar cycle: Jan, Feb, Mar
    "M": ["J", "K", "M"],  # Jun cycle: Apr, May, Jun
    "U": ["N", "Q", "U"],  # Sep cycle: Jul, Aug, Sep
    "Z": ["V", "X", "Z"],  # Dec cycle: Oct, Nov, Dec
}


def format_prodexp(product: str, expiry: str) -> str:
    """Compose the product+expiry prefix, e.g. ("SR3", "Z6") -> "SFRZ6".

    `expiry` is the compact letter+digit form. Any month code (F/G/H/.../Z)
    plus a single year digit is accepted — monthlies are valid expiries.
    """
    if product not in VALID_PRODUCTS:
        raise ValueError(f"unknown product {product!r}; expected one of {sorted(VALID_PRODUCTS)}")
    if len(expiry) != 2 or expiry[0] not in VALID_MONTH_CODES or not expiry[1].isdigit():
        raise ValueError(f"invalid expiry {expiry!r}; expected e.g. 'Z6', 'V6', 'U7'")
    return f"{PRODUCT_TICKERS[product]}{expiry}"


def monthly_expiries_for(expiry: str) -> list[str]:
    """Given a quarterly expiry like 'Z6', return all 3 monthlies rolling
    into it in order: ['V6', 'X6', 'Z6']. If the expiry is a non-quarterly
    monthly, returns just [expiry] (we don't expand mid-cycle).
    """
    if len(expiry) != 2:
        return [expiry]
    month, year = expiry[0], expiry[1]
    if month not in VALID_QUARTERLY_CODES:
        return [expiry]
    return [f"{m}{year}" for m in MONTHLIES_FOR_QUARTERLY[month]]
