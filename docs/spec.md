# v1 Specification — STIR Options Structure Generator

Frozen as of 2026-04-21. Changes go through a spec amendment; do not edit silently.

## 1. Goal

Desk member types a rates-options scenario in natural language. Tool emits a grouped, ready-to-paste block of PricingMonkey trade-description strings covering the structures implied by the scenario. Desk member copies and pastes into PM. PM prices and validates.

## 2. Inputs

### 2.1 Scenario (free text)

Casual, abbreviated, possibly incomplete. Examples:

- `fade hawkish fomc sfrz6 tight around 97, flies and condors`
- `I want to fade a hawkish FOMC in SFRZ6, target the 97.00 level tightly, show me flies and condors including broken variants`
- `ecb dovish er march, cheap downside`
- `bullish sonia k6 around 96.25, show me cheap stuff`

### 2.2 Parsed params (LLM parser output — strict JSON)

```json
{
  "product": "SR3" | "ER" | "SFI" | "0Q" | "0R" | "0N",
  "expiry": "Z6" | "U6" | "M6" | "H7" | ...,
  "anchor_price": 97.00,
  "directional_view": "bullish_price" | "bearish_price" | "neutral",
  "families": ["fly", "condor"] | null,
  "tightness": "tight" | "medium" | "wide" | null,
  "cost_preference": "cheap" | "normal" | null,
  "broken_direction_flag": "in_favour" | "against" | null,
  "raw_scenario": "<original text>"
}
```

Fields the parser **must not** populate beyond verbatim extraction:

- `anchor_price` — convert from rate if user spoke in rates (price = 100 − rate). Numbers <10 treated as rate, ≥10 as price.
- `families` — `null` if user did not specify; the enumerator picks a sensible default.
- `broken_direction_flag` — populated only if user said "in favour" / "against" / "in my favour" / similar. Never inferred.

Parser never decides wing widths, strikes, ratios, or which variants to produce.

## 3. Product → scenario mapping

Only used to pick a product when the scenario names a macro event but not a product. Picks one:

| Event keyword | Product |
|---|---|
| FOMC, Fed, NFP, CPI (US), PCE | `SR3` |
| ECB, CPI (Euro, EZ), HICP | `ER` |
| BoE, MPC, CPI (UK), gilt-adjacent | `SFI` |

If the scenario explicitly names a product ticker (`SFRZ6`, `ERU6`, etc.), the ticker wins.

## 4. Directional-language mapping

| Language | `directional_view` |
|---|---|
| "rates lower", "bullish", "fade selloff", "dovish", "cut", "dovish surprise" | `bullish_price` |
| "rates higher", "bearish", "fade rally", "hawkish", "hike", "hawkish surprise" | `bearish_price` |
| "pin", "range-bound", "sideways", no directional word | `neutral` |

Direction is always in **price** terms to avoid confusion (a bullish-price view = rates-lower view).

## 5. Outputs

### 5.1 Preview pane (tkinter)

Grouped by structure type with headings. Example for `fade hawkish fomc sfrz6 tight around 97, flies and condors`:

```
=== Call Flies (symmetric) ===
SFRZ6 96.93/97.00/97.06 c fly
SFRZ6 96.87/97.00/97.12 c fly

=== Call Flies (broken, in favour) ===
SFRZ6 96.87/97.00/97.06 c fly
SFRZ6 96.81/97.00/97.06 c fly

=== Call Condors (symmetric) ===
SFRZ6 96.87/96.93/97.06/97.12 c condor
```

Note: `fade hawkish FOMC` = fade a hawkish surprise = bullish-price view. Calls (not puts) are the natural expression. Structure family choice follows the user; variant selection follows the desk rules in §8.

### 5.2 Clipboard (Copy button)

Only the trade-description lines are copied. No headings, no blank lines between groups. PM receives one structure per line.

### 5.3 Paste target

User's responsibility. User Ctrl+V into the Trade Description column of their PM blotter. Append at bottom of existing rows (not overwrite).

## 6. Strike grids and snapping

| Product | Grid | Written as |
|---|---|---|
| SOFR (SR3, 0Q) | 6.25bp | 2 decimals (`97.06`, `96.93`) |
| Euribor (ER, 0R) | 6.25bp | 2 decimals (`97.06`, `96.81`) |
| SONIA (SFI, 0N) | 5bp | 2 decimals (`96.25`, `96.30`) |

**Display rule (all products):** strikes walk the product's grid internally but are written with 2 decimal places in the trade description. `97.0625` → `97.06`, `97.1875` → `97.18`, `96.8125` → `96.81`, `96.9375` → `96.93`. PM accepts both forms; the desk uses 2-decimal for readability. The enumerator truncates at emit time; internal arithmetic stays on the grid.

Enumerator snaps the user's anchor to the nearest grid point, then walks the grid for wings/wider variants. PM validates — if a strike isn't actually listed, PM renders the row red. The tool does not duplicate PM's listed-strike validation.

## 7. Structure families — what the enumerator can produce

| Family | Variants in v1 |
|---|---|
| Outright call / put | ATM, ±1 grid step, ±2 grid steps |
| Call spread / put spread | 1-step, 2-step, 3-step wides |
| Call fly / put fly | Symmetric (narrow, medium, wide); broken in-favour (multiple asymmetries); broken against (multiple asymmetries) |
| Call condor / put condor | Symmetric (narrow, wide); broken in-favour; broken against |
| Ratio spread | 1×2 cs/ps, 1×3 cs/ps |
| Ratio fly | 1×3×2, 1×2.5×1, 1×1.5×0.5 symmetric-strike variants |
| Risk reversal | K±1 step, K±2 steps (put strike / call strike) |
| Straddle | At anchor; ±1 step if anchor not on grid |
| Strangle | ±1, ±2 steps from anchor |
| Calendar | Same strike, current vs next listed quarterly |

Canonical emission form: see [pm_grammar.md §v1 scope](pm_grammar.md).

## 8. Desk rules for broken flies/condors

**"In favour"** = cost economics, not payoff geometry. The wider wing goes on the side the market is coming from, cheapening the structure or bringing it to credit.

### 8.1 Broken fly — in favour

**Bullish-price view:** lower wing wider than upper wing. Body closer to upper strike.

Concrete tuples at SFRZ6 anchor 97.00 (from user; 2-decimal form):
- `SFRZ6 96.87/97.00/97.06 c fly` — lower 12.5bp, upper 6.25bp
- `SFRZ6 96.81/97.00/97.06 c fly` — lower 18.75bp, upper 6.25bp
- `SFRZ6 96.81/97.00/97.12 c fly` — lower 18.75bp, upper 12.5bp

**Bearish-price view:** upper wing wider than lower wing. Body closer to lower strike.

Concrete tuples at ERZ6 anchor 97.00 (from user):
- `ERZ6 97.12/97.00/96.93 p fly` — upper 12.5bp, lower 6.25bp (asymmetric: upper wider)
- (second confirmed tuple pending — see §8.4)

### 8.2 Broken fly — against

"Against" = mirror of "in favour." Same widths and asymmetries, wing sides swapped.

- Bullish-price against: upper wing wider than lower. Body closer to lower strike.
- Bearish-price against: lower wing wider than upper. Body closer to upper strike.

Implementation: the enumerator builds the in-favour list, then mirrors strike indices around the anchor to produce the against list. A unit test asserts `mirror(mirror(x)) == x` and that in-favour / against tuples are distinct.

### 8.3 Broken condor — same principle

Four strikes `K1<K2<K3<K4` where `K2,K3` are the body and `K1,K4` are the wings. "In favour" for a bullish view = wider lower wing distance `K2−K1` than upper wing distance `K4−K3`. Same mirroring rule for "against."

### 8.4 Open items

- User to supply a second confirmed `bearish broken-in-favour` tuple at ERZ6 anchor 97.00 when the enumerator is built, so the unit-test fixture has at least two concrete asymmetries per direction-view pair. Not blocking; can be closed when we get there.
- User to supply three concrete `bullish broken-against` tuples at SFRZ6 anchor 97.00 when the enumerator is built, OR confirm "against = exact mirror of in-favour" so the code can mirror programmatically. Not blocking.

## 9. Tightness → width mapping

Placeholder. User to dictate concrete mapping from `tightness` ∈ {tight, medium, wide, null} → wing-step counts per family. Until then, enumerator emits the full narrow/medium/wide variant set and ignores the `tightness` field with a TODO comment.

## 10. Cost preference → family selection

Placeholder. User to dictate which families count as "cheap" (likely: broken-in-favour flies, 1×2 cs sold, far OTM strangles, etc.). Until then, `cost_preference` passes through and the enumerator tags groups with `(cheap)` / `(normal)` in the preview heading so the user can eyeball. User will refine.

## 11. Default families when scenario doesn't specify

If `families == null`: outrights (ATM call + ATM put), 1-step call spread, 1-step put spread, symmetric call fly at anchor. Small cross-section so the user always gets something useful; preview heading notes `(default set — scenario did not specify)`.

## 12. Variant counts (to prevent spam)

Soft cap of ~20 structures total per run. If the scenario implies more variants than that (e.g. "give me everything"), preview shows a truncation note and the user can re-run with more specificity. Enumerator is deterministic in its ordering so the same scenario always produces the same output.

## 13. GUI behaviour

- Scenario textbox: multi-line, ~6 lines tall, placeholder text shows an example.
- Generate button: triggers `claude -p` subprocess on a background thread; button disables and shows "Parsing…" while running.
- Preview: read-only text pane; grouped with headings.
- Copy to clipboard: copies trade-description lines only (no headings, no blank lines).
- Error state: if `claude -p` fails (timeout, non-zero exit, invalid JSON, rate limit), preview shows the raw error and a "Retry" button. No silent fallback.

## 14. Out of scope for v1

- Reading back PM prices/Greeks into the tool
- Driving the browser / pasting into PM automatically
- Weekly flow analysis (future scope)
- USTs, EGBs, Gilts, OTC
- Delta hedges, relative strikes, synthetic strikes, structure-vs-structure in PM grammar (we emit explicit strikes on listed grids)
- Non-quarterly expiries in the whites (only H/M/U/Z)
- Persistence of past scenarios / history / favourites

## 15. Golden tests

Ten to fifteen hand-labelled scenarios with expected parsed-JSON output, run before any change to the prompt or the skill file. Lives in `tests/golden/`. Scenarios authored by the user; test runner asserts the parser output matches exactly.
