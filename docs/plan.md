# Implementation Plan

Build order. One file at a time. Each step gets committed and user-confirmed before the next starts.

Assumes [CLAUDE.md](../CLAUDE.md), [docs/pm_grammar.md](pm_grammar.md), and [docs/spec.md](spec.md) are signed off.

## Stage 0 — Repo skeleton

**Goal:** working Python project that runs, even if it does nothing yet.

1. `pyproject.toml` — Python 3.11+, deps: `pyperclip`. Dev deps: `pytest`. No `anthropic` SDK (we shell out to `claude -p`).
2. `.gitignore` — standard Python + `.venv/`, `__pycache__/`, `.pytest_cache/`, `dist/`, `build/`.
3. `src/kcp_structgen/__init__.py` — empty package marker.
4. `README.md` — one-paragraph what-this-is + run instructions (`python -m kcp_structgen`).

**Done when:** `pip install -e .` works; `python -c "import kcp_structgen"` returns without error.

## Stage 1 — Enumerator core (no LLM, no GUI)

Build the deterministic heart first. No tkinter, no `claude -p`, no clipboard. Just: params → list of PM strings. Fully unit-tested before anything else touches it.

5. `src/kcp_structgen/products.py` — product codes, month codes, expiry formatting. `SFR`, `ER`, `SFI`, `0Q`, `0R`, `0N`; month map `{3:'H', 6:'M', 9:'U', 12:'Z'}`; single-digit year. Function `format_prodexp(product, expiry) -> str` (e.g. `("SR3", "Z6") -> "SFRZ6"`).

6. `src/kcp_structgen/strikes.py` — strike grids and formatting.
   - `GRID_BP = {"SR3": 6.25, "0Q": 6.25, "ER": 6.25, "0R": 6.25, "SFI": 5.0, "0N": 5.0}`
   - `snap_to_grid(price, product) -> float` — snaps to nearest grid point.
   - `format_strike(price, product) -> str` — SOFR/SONIA full decimals; Euribor truncated to 2 decimals (the display rule from spec §6).
   - `walk(anchor, product, n_steps) -> list[float]` — returns grid points at `anchor ± k*step` for `k in 0..n_steps`.
   - Unit tests: round-trip `snap(format(x)) == x`; Euribor display `97.0625 -> "97.06"`, `97.1875 -> "97.18"`.

7. `src/kcp_structgen/enumerator.py` — the heart. One function per family, each returning `list[str]` of PM trade-description strings.
   - `outrights(params) -> list[str]`
   - `verticals(params) -> list[str]`
   - `flies_symmetric(params) -> list[str]`
   - `flies_broken_in_favour(params) -> list[str]` — concrete tuples per spec §8.1, built by mirroring the in-favour rule across `directional_view`.
   - `flies_broken_against(params) -> list[str]` — mirrored from in-favour per spec §8.2.
   - `condors_symmetric(params) -> list[str]`
   - `condors_broken_in_favour(params)`, `condors_broken_against(params)`
   - `ratio_spreads(params)`, `ratio_flies(params)`
   - `risk_reversals(params)`, `straddles(params)`, `strangles(params)`, `calendars(params)`
   - A top-level `enumerate_structures(params) -> list[Group]` where `Group = {"heading": str, "lines": list[str]}` routes by `params["families"]` (or the default set if None) and obeys the variant cap (spec §12).

8. `tests/test_strikes.py` — unit tests for grid math and formatting.

9. `tests/test_enumerator.py` — locks the concrete strike tuples from spec §8.1 as expected outputs. This is the regression wall for desk conventions.

**Done when:** `pytest` green; calling `enumerate_structures` with a hand-built params dict produces the expected grouped output matching spec §5.1 exactly.

## Stage 2 — NL parser

Build the LLM bridge on top of a working enumerator, so when the parser goes wrong we know the failure is in parsing, not enumeration.

10. `src/kcp_structgen/parser.py`:
    - `parse_scenario(text: str) -> dict` — shells out to `claude -p --output-format json` with a system prompt that locks the output schema per spec §2.2.
    - Subprocess invocation, timeout, JSON validation, clear error messages on failure. No silent fallbacks.
    - The system prompt itself is loaded from `src/kcp_structgen/prompts/parse_scenario.md` so it's diffable and versioned.

11. `src/kcp_structgen/prompts/parse_scenario.md` — the parse prompt. Short, explicit schema, a few worked examples pulled from spec §2.1. Makes clear what the parser does NOT do (no strike math, no family selection, no wing direction).

12. `tests/golden/` — 10–15 hand-labelled scenarios as `.json` files, each with `{"scenario": "...", "expected": {...}}`. User authors these.

13. `tests/test_parser_golden.py` — runs every golden file through `parse_scenario` and asserts exact JSON match. Gates prompt changes.

**Done when:** golden tests green; a new scenario flows `text → params` cleanly.

## Stage 3 — GUI and clipboard

Wire parser + enumerator to a tkinter window.

14. `src/kcp_structgen/gui.py`:
    - Scenario textbox (multi-line, placeholder).
    - Generate button → spawns parser on a background thread; button disables, shows "Parsing…".
    - On parser return: calls `enumerate_structures`, renders grouped preview.
    - Copy button → `pyperclip.copy(lines_only)` (no headings).
    - Error pane on parser failure with retry.

15. `src/kcp_structgen/__main__.py` — `python -m kcp_structgen` launches the GUI.

**Done when:** double-clickable launch on a clean Windows PC runs the tool end-to-end.

## Stage 4 — Packaging for the desk

Deployment to non-engineer colleagues. Built only after Stages 0–3 are working and user-validated on own machine.

16. Decide: `pyinstaller` single-file exe, or a `.bat` wrapper that assumes Python + Claude Code installed. User preference.
17. `docs/install.md` — step-by-step for a non-technical trader. Screenshots if useful.

## Open items to resolve during build (not blocking)

- One more confirmed `bearish broken-in-favour` ER tuple (spec §8.4)
- Three confirmed `bullish broken-against` SFR tuples, OR confirmation that "against = exact mirror of in-favour" (spec §8.4)
- `tightness` → wing-step count mapping (spec §9)
- `cost_preference` → family weighting (spec §10)

These get asked and encoded during Stage 1 when the enumerator is being built. None of them block starting the plan.

## Working rules for build

- One file per commit. User reviews before next file.
- Any desk-convention rule encoded as a comment block + unit test with concrete user-supplied tuples. No reasoning from first principles.
- No file gets written that isn't referenced by a test or by `__main__.py`. No dead code.
- When a golden test would catch a prompt regression, it gets added before the prompt change, not after.
