# STIR Options Structure Generator


    
            ██╗  ██╗ ██████╗██████╗     ███████╗████████╗██╗██████╗ 
            ██║ ██╔╝██╔════╝██╔══██╗    ██╔════╝╚══██╔══╝██║██╔══██╗
            █████╔╝ ██║     ██████╔╝    ███████╗   ██║   ██║██████╔╝
            ██╔═██╗ ██║     ██╔═══╝     ╚════██║   ██║   ██║██╔══██╗
            ██║  ██╗╚██████╗██║         ███████║   ██║   ██║██║  ██║
            ╚═╝  ╚═╝ ╚═════╝╚═╝         ╚══════╝   ╚═╝   ╚═╝╚═╝  ╚═╝
            
            ██████╗ ██████╗  ██████╗ ████████╗ ██████╗ ████████╗██╗   ██╗██████╗ ███████╗     ██╗
            ██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝██╔═══██╗╚══██╔══╝╚██╗ ██╔╝██╔══██╗██╔════╝    ███║
            ██████╔╝██████╔╝██║   ██║   ██║   ██║   ██║   ██║    ╚████╔╝ ██████╔╝█████╗      ╚██║
            ██╔═══╝ ██╔══██╗██║   ██║   ██║   ██║   ██║   ██║     ╚██╔╝  ██╔═══╝ ██╔══╝       ██║
            ██║     ██║  ██║╚██████╔╝   ██║   ╚██████╔╝   ██║      ██║   ██║     ███████╗     ██║
            ╚═╝     ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝    ╚═╝      ╚═╝   ╚═╝     ╚══════╝     ╚═╝

## What this is

A desktop tool for a rates desk. Desk member types a trade scenario in natural language; the tool emits a grouped list of listed STIR option structures in PricingMonkey (PM) trade-description grammar. User clicks Copy, pastes into PM, and PM does all pricing, Greeks, and strike validation.

## Who uses it

Four rates desk traders. None are software engineers (the project owner is the most technical). Day-to-day use must not require opening a terminal, editing config, or touching code.

## Architecture (locked — do not re-litigate without explicit ask)

```
[tkinter window]
    scenario text box → Generate button
         │
         ▼
[claude -p --output-format json]  (subprocess; Enterprise-seat-covered)
    NL → structured params (product, expiry, anchor_price, directional_view,
                            families, tightness, cost_preference, flags)
         │
         ▼
[Python enumerator]  (deterministic; owns ALL desk conventions)
    params → grouped list of PM trade-description strings
         │
         ▼
[preview pane with headings]  +  [Copy to clipboard button]
         │
         ▼
User Ctrl+V into PricingMonkey tab in Chrome (PM prices + validates)
```

**Rejected and not to be re-proposed:**

- Browser automation of any flavour (Selenium, Playwright, CoWork, computer-use)
- Direct Anthropic SDK calls (per-call API billing)
- Local LLM (no GPU on work PCs)
- PM-cell input / PM readback
- Any NL parsing inside the enumerator or desk conventions inside the prompt

## Responsibility split

**LLM parser (`claude -p`):** mushy natural language → clean params. Extraction only. No arithmetic, no desk conventions, no structure enumeration, no wing-direction reasoning.

**Python enumerator:** params → PM trade-description strings. All desk rules live here as explicit code with comments and unit tests. Anything that requires a desk convention — wing direction for "in favour" vs "against", which families count as "cheap", tightness → width mappings, how many variants per family — is Python, not prompt.

## Hard constraints

1. **No per-call API charges at runtime.** `claude -p` subprocess only; no `from anthropic import Anthropic`.
2. **No browser automation.** Clipboard paste via `pyperclip`; user Ctrl+V themselves.
3. **No local options math.** No Black-76, no vol surface, no Greeks. PM prices everything.
4. **Windows + Chrome + Claude Code + PM-in-browser** is the entire runtime target.
5. **Non-engineer users.** Install and use without CLI. Tkinter window double-click launch.

## Desk convention principle

Desk conventions can invert general options intuition. Example burned us already: "broken in favour" refers to **cost economics, not payoff geometry** — for a bullish view, the lower wing is wider than the upper wing (body closer to the upper strike), which is the *opposite* of what payoff-geometry reasoning would pick.

**Rule:** when encoding a rule that touches desk convention, ask the user. Do not reason from first principles. Encode the answer as Python with a comment block and unit tests locking concrete strike tuples. The user's tuples are the source of truth; the rule statement is the documentation.

## Products in v1 scope

- **SOFR** (`SFR`) whites + 1y mid-curve (`0Q`)
- **Euribor** (`ER`) whites + mid-curve (`0R`)
- **SONIA** (`SFI`) whites + mid-curve (`0N`)

Whites = front 4 quarterlies (H/M/U/Z).

**Strike grids:** SOFR 6.25bp, Euribor 6.25bp, SONIA 5bp.

**Monthlies and underlying quarterlies.** Each quarterly (H/M/U/Z) has 3 monthly option expiries that share its underlying future: Z cycle = V/X/Z, U cycle = N/Q/U, M cycle = J/K/M, H cycle = F/G/H. **The futures-price dialog always asks for the underlying quarterly's price, never the monthly's** — there is no separate ERQ6 future; ERQ6 options settle into ERU6 futures. See `products.py::underlying_quarterly`.

**Out of v1 scope:** USTs, EGBs, Gilts, OTC.

## Structure families in v1

Outrights, vertical spreads, flies (symmetric + broken in-favour + broken against), condors (symmetric + broken), ratio spreads (1×2, 1×3), ratio flies (1×3×2, 1×2.5×1, etc.), risk reversals, straddles, strangles, calendars/diagonals.

## Working conventions

- **Plan fully first, one file at a time.** Do not write file N+1 before the user confirms file N.
- **Golden tests gate prompt/skill changes.** 10–15 hand-labelled scenarios with expected JSON output.
- **Ask for desk conventions.** Never infer. Concrete tuples from the user become unit tests.
- **No sycophancy.** If the user is wrong or a suggestion would regress an earlier decision, say so.
- **Small commits, working code at each step.**
- **Portable context in the repo.** All load-bearing project context lives in `CLAUDE.md` and `docs/` so a session can be resumed from any machine with a clone.

## Prototype-phase behaviours (replace when blpapi is wired)

- **Always ask for the current futures price.** `scenario_needs_current_price()` returns True unless the user explicitly supplied an `anchor_price` number. `current_rates.json` holds the cash rate, not the futures price, and the two diverge whenever the curve prices in an expected move. The futures price is what decides call-vs-put direction in `_is_bullish`/`_is_bearish`.
- **Dialog asks for the underlying quarterly.** For any monthly expiry (V/X, N/Q, J/K, F/G), the dialog resolves to its quarterly via `products.underlying_quarterly()` and asks for that. "What is the current ERU6 futures price?" even when the option is Q6.
- **Replace both behaviours** when the blpapi feed is live — the tool will pull the futures price itself and the dialog goes away.

## Parser contract

- The LLM must return a **raw JSON object**, first character `{` and last character `}`. No markdown fences, no prose. Prompt enforces this explicitly.
- The parser strips a leading ` ```json ... ``` ` fence defensively before JSON-decoding, for the times the model ignores the instruction.
- Subprocess timeout is 120s (first `claude -p` call after login can be slow).

## Layer 2 status (as of 2026-05-19)

- Spec frozen at [docs/layer2_spec.md](docs/layer2_spec.md), 22 sections including charts addendum.
- **Pipeline complete and tested end-to-end with mocks**: 15 of 19 build steps done, 324 tests passing. All non-UI modules built: cme_loader → flow_loader → events_api → event_matcher → classifier → segmenter → aggregator → fomc_scraper → memory → commentary_loader → prompts/weekly_analysis.md → runner.
- **`runner.py`** is the single entry: `generate_weekly_rundown(week_d) -> RunResult` orchestrates everything. Never raises; all failure modes degrade to warnings. Returns the markdown rundown, the structured digest sent to the LLM, saved paths, and a warnings list.
- **Next step**: dry-run on a real past week of data BEFORE building the GUI. See "Layer 2 dry-run setup" below.
- **GUI tab**: not built yet. Mechanical work once the pipeline is producing good output on real data.
- **Charts**: deferred; planned to add as an addendum after first real runs show what visualisations actually help.
- v2 deferrals tracked in [docs/v2_backlog.md](docs/v2_backlog.md): auto-download CME, evidence-scoring/tiering, ER/SFI expansion, weeklies + 2Y-5Y mid-curves, Bloomberg ECO CSV fallback, commentary_loader PDF support, real-week refinement run.

## Layer 2 dry-run setup

Before building the GUI, run `generate_weekly_rundown()` on a real past week to surface real-world issues and tune the prompt. Requirements:

1. **CME files** at `data/oi/daily/YYYY-MM-DD.xls` (or pre-parsed `.json`) for 5 trading days of the target week.
2. **Flow Excel** at `data/flow/flow.xlsx` — desk-maintained schema: `date, raw_note, product, expiry, structure, size, direction, price`.
3. **Client trades** at `data/client_trades/client_trades.xlsx` — same schema, different stream.
4. **Commentary** at `data/commentary/raw/<YYYY-MM-DD>/itc_us_morning.docx` + `mni_european_open.docx` per trading day. Copy-paste from email into Word. Loader is case-insensitive on filename.
5. **FMP key** at `config/settings.json` (copy from `config/settings.example.json` and paste the key — never commit).
6. **`claude` CLI** logged in and reachable from terminal.

Then from a Python shell:

```python
from datetime import date
from pathlib import Path
from kcp_structgen.analysis.runner import generate_weekly_rundown
result = generate_weekly_rundown(date(2026, 4, 17), data_root=Path("data"))
print("--- WARNINGS ---")
for w in result.warnings: print(" -", w)
print("--- RUNDOWN ---")
print(result.rundown_md)
```

The pipeline saves three artefacts per run to `data/weekly_digests/`:
- `<week>.json` — structured digest the LLM saw
- `<week>_headlines.md` — extracted headlines, used as next week's prior-week context
- `<week>_rundown.md` — full markdown rundown

The digest persists even if the LLM call fails, so you can inspect what got built.

## Charts (post-spec addendum, not yet in spec)

User asked about charts during the Layer 2 design. Decision: LLM emits chart spec JSON blocks within the markdown rundown, Python module `analysis/charts.py` renders them via matplotlib to PNGs, post-processor swaps the JSON blocks for image links. LLM picks *which* charts; Python owns *how* they render. Charts must come from a fixed enum (`bar`, `line`, `stacked_bar`, `heatmap`) — no free-form. Pin specific chart types when user describes what visualisations they actually want. Add this as a §22 to layer2_spec.md when freezing.

## Known runtime issues (Layer 1)

- **`claude -p exited 1, stderr: <empty>`** — almost always a session/auth issue. Fix: open a terminal, run `claude` (no args), let it complete an auth check or login, then retry the GUI. The CLI sometimes silently expires its session after long idle periods.
- **First call after fresh login is slow.** Timeout is set to 120s for this reason — do not lower.
- **GUI swallows stderr when empty.** Improvement filed for later: surface the raw stderr/stdout in the error panel so future debugging doesn't require running the CLI manually.

## Key files

- `CLAUDE.md` (this file) — root-level project context
- `docs/pm_grammar.md` — PricingMonkey trade-description grammar + v1 canonical forms
- `docs/spec.md` — frozen v1 spec (inputs, outputs, enumerator rules)
- `docs/plan.md` — implementation plan, file-by-file
- `PM_Trade_Input.docx` — original PM grammar doc (source for `pm_grammar.md`)

## Future scope (not v1 — don't architect it out)

Weekly flow analysis add-on: read flagged flows from PM export, append to persistent log, generate weekly rundown via `claude -p`. Built after v1 is live.
