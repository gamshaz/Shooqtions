"""Parse a CME VoI .xls file into a structured digest.

Input: legacy binary `.xls` (OLE) from CME's free Volume & Open Interest report
for Interest Rate Options. See docs/layer2_spec.md §5.1 for file shape.

Output: a JSON-serialisable dict with one day of activity, scoped per
docs/layer2_spec.md §4 (SR3 + 0Q only; quarterlies + their monthlies; futures
restricted to quarterlies).

No LLM call. Deterministic. Downstream modules (segmenter, aggregator) consume
the dict and never read the raw .xls.

Note: the CME file does NOT contain futures settle prices, despite our earlier
spec assumption. The Futures section's `At Close` column is futures open
interest. Settle prices would require a separate `STLPRICE` daily file —
deferred to a v2.1 enhancement. See spec §10.1.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

SHEET_NAME = "VOI Details Report"

# Section markers we care about. Other OPTION TYPE: sections (weeklies, 2Y-5Y,
# monthly First/Second variants) are skipped.
SECTION_MARKERS = {
    "futures":  "Futures",
    "SR3":      "OPTION TYPE: American Options",
    "0Q":       "OPTION TYPE: 1 Year Mid-Curve Options",
}

# Month letter codes (CME futures convention).
MONTH_CODES: dict[str, str] = {
    "JAN": "F", "FEB": "G", "MAR": "H", "APR": "J",
    "MAY": "K", "JUN": "M", "JUL": "N", "AUG": "Q",
    "SEP": "U", "OCT": "V", "NOV": "X", "DEC": "Z",
}

# In-scope quarterlies (futures + option underlying cycles). 2026 + 2027.
QUARTERLY_CODES = ("H", "M", "U", "Z")
YEARS_IN_SCOPE = ("6", "7")

# In-scope option expiries: all 12 months for the years in scope.
# Loader filters out anything not in the §4.2 cycles (the 24 cycles of
# F/G/H, J/K/M, N/Q/U, V/X/Z for years 6 and 7).
MONTHLY_LETTERS_IN_SCOPE = frozenset({
    "F", "G", "H",   # H cycle
    "J", "K", "M",   # M cycle
    "N", "Q", "U",   # U cycle
    "V", "X", "Z",   # Z cycle
})

FILENAME_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
EXPIRY_LABEL_RE = re.compile(r"^([A-Z]{3}) (\d{2}) (Calls|Puts)$")
STRIKE_RE = re.compile(r"^\d{4,5}$")
THOUSANDS_INT_RE = re.compile(r"^-?[\d,]+$")


class CMELoaderError(ValueError):
    """Parser failure: missing sheet, missing required section, bad filename."""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_strike(raw: str) -> float:
    """Convert CME strike string (`'9631'`) to price (`96.31`).

    Strikes are 4- or 5-digit integers in implied-decimal form:
        9631 -> 96.31  (SR3 6.25bp grid)
        10100 -> 101.00
    """
    if not STRIKE_RE.match(raw):
        raise ValueError(f"bad strike token: {raw!r}")
    return float(raw) / 100.0


def _parse_int(raw) -> int:
    """Convert a CME thousands-separated number string to int.

    Handles `'1,735'`, `'-3,986'`, `'0'`, `nan`/`None` (→ 0). Raises on
    anything else so we don't silently swallow real corruption.
    """
    if raw is None:
        return 0
    if isinstance(raw, float) and pd.isna(raw):
        return 0
    if isinstance(raw, (int,)):
        return int(raw)
    if isinstance(raw, float):
        return int(raw)
    s = str(raw).strip()
    if s == "" or s.lower() == "nan":
        return 0
    if not THOUSANDS_INT_RE.match(s):
        raise ValueError(f"bad integer token: {raw!r}")
    return int(s.replace(",", ""))


def _expiry_code(month_label: str, year_label: str) -> str:
    """Convert (`'MAY'`, `'26'`) -> `'K6'`."""
    letter = MONTH_CODES.get(month_label.upper())
    if letter is None:
        raise ValueError(f"unknown month label: {month_label!r}")
    return f"{letter}{year_label[-1]}"


def _expiry_in_scope(expiry: str) -> bool:
    """True if expiry (e.g. `'K6'`) is in §4.2 scope."""
    if len(expiry) != 2:
        return False
    letter, year = expiry[0], expiry[1]
    return letter in MONTHLY_LETTERS_IN_SCOPE and year in YEARS_IN_SCOPE


def _quarterly_in_scope(expiry: str) -> bool:
    """True if expiry is a §4.1 in-scope quarterly future."""
    if len(expiry) != 2:
        return False
    return expiry[0] in QUARTERLY_CODES and expiry[1] in YEARS_IN_SCOPE


def _trade_date_from_filename(path: Path) -> date | None:
    m = FILENAME_DATE_RE.search(path.stem)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return date(y, mo, d)


# ---------------------------------------------------------------------------
# Section scanning
# ---------------------------------------------------------------------------

def _find_section_starts(df: pd.DataFrame) -> dict[str, int]:
    """Return row indices for each section start marker we care about.

    `Futures` is matched as a standalone row. `OPTION TYPE: ...` markers
    must match exactly.
    """
    starts: dict[str, int] = {}
    for i in range(len(df)):
        v = df.iat[i, 0]
        if pd.isna(v):
            continue
        s = str(v).strip()
        if s == "Futures":
            starts["futures"] = i
        elif s == SECTION_MARKERS["SR3"]:
            starts["SR3"] = i
        elif s == SECTION_MARKERS["0Q"]:
            starts["0Q"] = i
        elif s.startswith("OPTION TYPE:") and "SR3" in starts and "0Q" not in starts:
            # Boundary between SR3 options and the *next* section (whatever it is).
            # Mark SR3 end here so we don't accidentally read into the next type.
            starts.setdefault("_SR3_end", i)
        elif s.startswith("OPTION TYPE:") and "0Q" in starts and "_0Q_end" not in starts:
            starts.setdefault("_0Q_end", i)
    return starts


def _futures_block_end(df: pd.DataFrame, start: int) -> int:
    """Find the end of the Futures section (the row with `TOTALS` or the
    next blank-then-section marker). Inclusive of the start, exclusive of end."""
    for i in range(start + 1, len(df)):
        v = df.iat[i, 0]
        if pd.isna(v):
            continue
        s = str(v).strip()
        if s == "TOTALS":
            return i + 1   # include TOTALS row's predecessor but skip TOTALS itself in parse
        if s.startswith("OPTION TYPE:"):
            return i
    return len(df)


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_futures_section(df: pd.DataFrame, start: int, end: int) -> dict:
    """Walk rows [start, end), keep in-scope quarterlies only.

    Returns {expiry: {oi, oi_change, volume}}.
    """
    out: dict[str, dict] = {}
    for i in range(start + 1, end):
        v = df.iat[i, 0]
        if pd.isna(v):
            continue
        s = str(v).strip()
        if s in ("Month", "TOTALS", ""):
            continue
        # Expect "MMM YY" form
        parts = s.split()
        if len(parts) != 2 or parts[0] not in MONTH_CODES:
            continue
        month_lbl, year_lbl = parts
        try:
            expiry = _expiry_code(month_lbl, year_lbl)
        except ValueError:
            continue
        if not _quarterly_in_scope(expiry):
            continue
        row = df.iloc[i]
        try:
            out[expiry] = {
                "oi":        _parse_int(row.iat[10]),  # At Close
                "oi_change": _parse_int(row.iat[11]),  # Change
                "volume":    _parse_int(row.iat[4]),   # Total Volume
            }
        except ValueError as exc:
            # One bad row shouldn't kill the file; log via warning, skip.
            import warnings
            warnings.warn(f"skipping futures row {i} ({s!r}): {exc}")
            continue
    return out


def _parse_option_section(df: pd.DataFrame, start: int, end: int) -> dict:
    """Walk rows [start, end), build {expiry: {calls: [...], puts: [...]}}.

    Each `MMM YY Calls` / `MMM YY Puts` sub-block holds per-strike rows
    until the sub-block's `TOTALS` row (or the next block header).
    """
    out: dict[str, dict] = {}
    i = start + 1
    while i < end:
        v = df.iat[i, 0]
        if pd.isna(v):
            i += 1
            continue
        s = str(v).strip()
        m = EXPIRY_LABEL_RE.match(s)
        if not m:
            i += 1
            continue
        month_lbl, year_lbl, side = m.group(1), m.group(2), m.group(3)
        try:
            expiry = _expiry_code(month_lbl, year_lbl)
        except ValueError:
            i += 1
            continue
        # Drop expiries not in scope without parsing strikes.
        in_scope = _expiry_in_scope(expiry)
        # Move past the "Strike | Globex | ..." header row.
        # Find header row; from there read strike rows until TOTALS.
        i += 1
        # Header row is typically immediately after; skip if it is.
        if i < end and str(df.iat[i, 0]).strip() == "Strike":
            i += 1
        # Now read strike rows.
        strikes: list[dict] = []
        while i < end:
            cell = df.iat[i, 0]
            if pd.isna(cell):
                i += 1
                continue
            cs = str(cell).strip()
            if cs == "TOTALS":
                i += 1
                break
            if EXPIRY_LABEL_RE.match(cs) or cs.startswith("OPTION TYPE:"):
                # Block boundary — stop without consuming this row.
                break
            if not STRIKE_RE.match(cs):
                # Unknown row inside an option block; skip with a warning.
                import warnings
                warnings.warn(f"skipping unrecognised row {i} in option block: {cs!r}")
                i += 1
                continue
            if not in_scope:
                i += 1
                continue
            row = df.iloc[i]
            try:
                strikes.append({
                    "strike":    _parse_strike(cs),
                    "volume":    _parse_int(row.iat[4]),  # Total Volume
                    "oi":        _parse_int(row.iat[8]),  # At Close
                    "oi_change": _parse_int(row.iat[9]),  # Change
                })
            except ValueError as exc:
                import warnings
                warnings.warn(f"skipping option row {i} (strike {cs!r}): {exc}")
            i += 1
        if in_scope:
            block = out.setdefault(expiry, {"calls": [], "puts": []})
            key = "calls" if side == "Calls" else "puts"
            block[key].extend(strikes)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_cme_voi(xls_path: Path | str, trade_date: date | None = None) -> dict:
    """Parse a CME VoI `.xls` into the digest dict.

    `trade_date` is inferred from `xls_path` if its stem matches `YYYY-MM-DD`.
    Otherwise it must be supplied or `CMELoaderError` is raised.
    """
    path = Path(xls_path)
    if not path.is_file():
        raise CMELoaderError(f"file not found: {path}")

    if trade_date is None:
        trade_date = _trade_date_from_filename(path)
    if trade_date is None:
        raise CMELoaderError(
            f"could not infer trade_date from filename {path.name!r}; "
            "expected stem like '2026-04-22' or pass trade_date= explicitly"
        )

    try:
        df = pd.read_excel(path, sheet_name=SHEET_NAME, header=None)
    except ValueError as exc:
        raise CMELoaderError(
            f"failed to read sheet {SHEET_NAME!r} from {path.name}: {exc}"
        ) from exc

    starts = _find_section_starts(df)
    if "futures" not in starts:
        raise CMELoaderError(f"missing 'Futures' section in {path.name}")
    if "SR3" not in starts:
        raise CMELoaderError(
            f"missing '{SECTION_MARKERS['SR3']}' section in {path.name}"
        )

    fut_end = _futures_block_end(df, starts["futures"])
    futures = _parse_futures_section(df, starts["futures"], fut_end)

    sr3_end = starts.get("_SR3_end", starts.get("0Q", len(df)))
    sr3_options = _parse_option_section(df, starts["SR3"], sr3_end)

    options: dict[str, dict] = {"SR3": sr3_options}
    if "0Q" in starts:
        q0_end = starts.get("_0Q_end", len(df))
        options["0Q"] = _parse_option_section(df, starts["0Q"], q0_end)

    return {
        "trade_date": trade_date.isoformat(),
        "futures":    futures,
        "options":    options,
    }


def parse_and_save(xls_path: Path | str, out_dir: Path | str | None = None,
                   trade_date: date | None = None) -> Path:
    """Load + write to `<out_dir>/<stem>.json`. Returns the JSON path.

    If `out_dir` is None, writes next to the source `.xls`.
    """
    src = Path(xls_path)
    digest = load_cme_voi(src, trade_date=trade_date)
    dst_dir = Path(out_dir) if out_dir else src.parent
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{src.stem}.json"
    dst.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    return dst
