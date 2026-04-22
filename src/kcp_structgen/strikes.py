"""Strike grids, snapping, and 2-decimal display formatting.

Grids: SOFR/Euribor 6.25bp, SONIA 5bp. Internal arithmetic stays on the
grid; emission truncates to 2 decimals per the desk display rule (all
products). See docs/spec.md §6.
"""

from __future__ import annotations

from .products import VALID_PRODUCTS

GRID_BP: dict[str, float] = {
    "SR3": 6.25,
    "0Q":  6.25,
    "ER":  6.25,
    "0R":  6.25,
    "SFI": 5.0,
    "0N":  5.0,
}


def _grid_step(product: str) -> float:
    """Grid step in price units (e.g. 6.25bp = 0.0625)."""
    if product not in VALID_PRODUCTS:
        raise ValueError(f"unknown product {product!r}")
    return GRID_BP[product] / 100.0


def snap_to_grid(price: float, product: str) -> float:
    """Snap `price` to the nearest listed-strike grid point for `product`."""
    step = _grid_step(product)
    return round(round(price / step) * step, 10)


def format_strike(price: float, product: str) -> str:
    """Format `price` for a PM trade description: 2 decimals, truncated.

    Truncation (not rounding) matches desk convention — 97.0625 -> "97.06",
    97.1875 -> "97.18". The input is expected to already sit on the grid;
    this function just produces the display string.
    """
    snapped = snap_to_grid(price, product)
    # Truncate toward zero to 2 decimals. Small epsilon guards against
    # e.g. 97.06 being stored as 97.05999999999.
    truncated = int(snapped * 100 + 1e-9) / 100.0
    return f"{truncated:.2f}"


def walk(anchor: float, product: str, n_steps: int) -> list[float]:
    """Return `2*n_steps + 1` grid points centred on `anchor` (snapped).

    E.g. walk(97.00, "SR3", 2) -> [96.87, 96.93, 97.00, 97.06, 97.12]
    (grid values; display formatting via `format_strike`).
    """
    if n_steps < 0:
        raise ValueError("n_steps must be >= 0")
    step = _grid_step(product)
    centre = snap_to_grid(anchor, product)
    return [round(centre + k * step, 10) for k in range(-n_steps, n_steps + 1)]
