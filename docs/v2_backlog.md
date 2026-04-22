# v2 Backlog

All items deferred from Layer 1. Ordered roughly by desk priority.

---

## Parser improvements

- **Event-coverage mapping**: parse "before FOMC Sep", "into the Sep cycle" into a `horizon_event` and filter structures by that horizon (today the field is extracted but not consumed by the enumerator).
- **Rate path → condor body placement**: when `rate_events` gives a range (lo, hi terminal price), automatically widen condor body to span the range rather than sitting on a single anchor.
- **Richer cost_preference handling**: "give them", "collect", "pay no more than X ticks" → `cost_preference` + `max_payout_ticks` already parsed; enumerator today ignores `cost_preference`. Wire it up.
- **Product inference from event type**: "FOMC" already maps to SR3; add Riksbank → SR3-SEK hint, RBA → etc. when desk scope expands.
- **Calendar / diagonal strike grammar**: "buy Z6 call, sell H7 call at the same strike" — parser returns `family=calendar` but enumerator stub is empty.

---

## Enumerator improvements

- **Calendar / diagonal structures**: PM grammar for calendars and diagonals — needs desk input on preferred wings and expiry pairings.
- **Risk reversal**: currently emitted as single-strike lines; confirm PM grammar with desk.
- **Ratio fly variants**: 1×2.5×1, 1×3×2 etc. — confirm exact PM grammar.
- **Tightness → width table**: `tightness=tight/medium/wide` extracted by parser; enumerator today ignores it. Needs user-dictated width multipliers per family.
- **Cost-filter pass**: after enumeration, filter or rank by rough spread cost when `cost_preference=cheap`. Requires some cost heuristic (stub in code, actual rule from desk).
- **`horizon_event` expiry filter**: if `horizon_event=fomc_sep`, suppress expiries past Sep. Today it's a no-op.
- **`current_price_override` → call/put for condors**: direction logic in `_is_bullish/bearish` uses override correctly for flies; verify condor call/put labelling uses the same path.
- **Straddle / strangle strike logic**: currently placeholder; needs desk input on how many strikes away from ATM strangles go.

---

## GUI / UX

- **Settings panel**: editable `current_rates.json` values inside the GUI so the desk can update rates without touching a file.
- **History pane**: last N scenarios with one-click replay.
- **Anchor display in preview banner**: show the resolved anchor(s) alongside the scenario summary so the desk can sanity-check the price before copying.
- **Keyboard shortcut**: Ctrl+Enter to trigger Generate (today only the button works).
- **Copy without headings toggle**: checkbox next to Copy button (today headings are always included in clipboard output).
- **Error detail expansion**: click an error message in the status bar to see the full traceback in a popup.
- **Multi-anchor preview grouping**: when scenario resolves to 3 anchors (probabilistic), show each anchor as a collapsible section rather than interleaving all lines.

---

## Layer 2: Weekly flow analysis add-on

Separate workflow requested by user. Not part of the structure generator; described here for completeness.

- Read flagged trades from a PM export (CSV or copy-paste).
- Append to a persistent trade log (JSON or SQLite).
- Generate a weekly rundown via `claude -p`: "what did the desk put on this week, any themes?"
- Deliver as a formatted text block the PM-tab user can paste into a chat.

Architecture: same `claude -p` subprocess pattern. No extra API billing. Trigger via a second GUI tab or a separate script — to be decided.

---

## Infrastructure / packaging

- **One-click installer**: wrap into a Windows `.exe` via PyInstaller so the other three desk members never see a terminal.
- **Auto-update of `current_rates.json`**: scheduled task (Windows Task Scheduler) or a tiny tray app that pulls the rate once at open. Source: Bloomberg via DDE, or a PM screen-read — to be specced.
- **Test harness for parser golden tests**: golden tests today require live `claude -p` and are skipped in CI. Add a mock-subprocess fixture so the full golden suite can run offline.
- **Logging**: structured log of every `parse_scenario` call (input text + output JSON) to a local `.jsonl` file. Useful for debugging and eventual fine-tuning.
