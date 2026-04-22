# PricingMonkey Trade Description Grammar

Source: `PM_Trade_Input.docx` (Anthropic-owned PricingMonkey help doc). This file is a faithful markdown conversion plus a v1-scope section at the end listing the exact canonical forms the enumerator emits.

---

## Overview

Typing text in the **Trade Description** column is the primary way to drive what is modelled in PricingMonkey (PM). PM aims to support all terminology typically seen in chats; many different descriptions can resolve to the same structure.

PM will interpret as much of the input as possible and flag what it doesn't understand in red. To see how PM has interpreted a row, click anywhere in the row — the status bar at the bottom shows PM's parsed view (e.g. `tym ^` → `jun22 10y note 123.00 straddle`).

**Colour convention:**

- Blue: user input
- Black: output
- Red: unrecognised or invalid

## Examples (from PM docs)

- `jun ty 125 123 ps` — the 125/123 put spread in CBOT 10y note options for the next June expiry
- `1st monthly bund ^` — the straddle in the next monthly Eurex Bund expiry
- `gcz 25 delta fence` — 25-delta risk reversal in the next December Comex Gold expiry
- `sfrz6 97.50 put vs 97.40 30d` — CME SOFR Dec 2026 97.50 put hedged with 30% futures @ 97.40
- `jun spx 105% call` — CBOE SPX calls, next June expiry, struck at 105% of spot
- `6 month cl straddle` — synthetic straddle on NYMEX WTI Oil options expiring in 6 months

---

## Syntax reference

### Strategies (not exhaustive)

| Family | Accepted forms |
|---|---|
| Straddle | `straddle`, `strad`, `^` |
| Call spread / put spread | `call spread`, `cspd`, `cs`, `put spread`, `pspd`, `ps` |
| Call fly / put fly | `call fly`, `cfly`, `put fly`, `pfly` |
| 1×2 call/put spread | `1x2 call spread`, `1x2 cspd`, `1x2 cs` (and put equivalents) |
| 1×2×3 call/put fly | `1x3x2 call fly`, `1x3x2 cfly` (and put equivalents) |
| Weighted flies/spreads | `1x1.5 cs`, `1x2.5x1 cfly` |
| Ladders / trees | `call ladder`, `c lad`, `cl` |
| Combos / risk reversals | `combo`, `squash`, `fence`, `rr` |
| Strangle | `strangle`, `^` |

### Relative strikes

By default PM snaps to the nearest listed strike.

| Form | Example | Meaning |
|---|---|---|
| `X out` | `rxz 100 out call` | call 100 cents away from the underlying |
| `Xbp out` | `tyz 25bp out call` | call 25bp away from the underlying |
| `X delta` | `rxz 20 delta call` | call with 20% delta |
| `X%` | `sx5e dec25 105% call` | call struck at 1.05 × spot |
| `X% of forward` | `sx5e dec25 105% of forward call` | call struck at 1.05 × forward |
| `X wide` | `gcz5 100 wide fence` | 50-out call vs 50-out put |

### Rolling expiries

`1st`, `2nd`, `3rd` … enumerate contracts listed by the exchange in order. For monthly-only, use `1st monthly`, `2nd monthly`, etc. STIRs also accept `ED1 ^`, `ER2 ^` style for quarterlies.

Example: next 3 monthly Bund straddles → `1st monthly bund ^`, `2nd monthly bund ^`, `3rd monthly bund ^`.

### Relative expiries

Specify expiry in days, months, or years from today. PM interpolates between neighbouring listed expiries/strikes.

- `30 day rx ^` — ATM Bund straddle expiring in 30 days
- `6 month forward er` — Euribor future expiring in 6 months

### Structure vs structure

Use `vs` or a formula-style input.

- `sfr^ vs erz^` — Dec SOFR straddles vs Dec Euribor straddles
- `1*sfrh5^ - 1.5*sfrz6^` — 1× Mar25 SOFR straddles minus 1.5× Dec26 SOFR straddles

### Delta hedges

Append a hedge to the description. Supports:

| Form | Meaning |
|---|---|
| `... vs 98.51 40d` | futures at 98.51, 40% hedge ratio |
| `... x98.51 40d` | same as above, alternate notation |
| `... ref 98.51` | futures at 98.51, hedge ratio = current delta |
| `... delta hedged` | futures at market, hedge ratio = current delta |

---

## Other input columns

### Allow Synthetic Strike

When using relative strikes, PM snaps to the nearest listed strike by default. Checking **Allow Synthetic Strike** lets PM model theoretical strikes by interpolating between the nearest listed strikes.

Add via right-click → Insert Column → Input → Allow Synthetic Strike.

### Trade Amount, Trade Price, Trade Date

Populate these to PV and risk theoretical portfolios. When Trade Amount is set, the NPV column populates and risk columns show in cash terms. Trade Price feeds into NPV. NPV is zero before Trade Date on historical plots. Summing NPVs/risks across rows and right-click → Plot History constructs historical PnL/risk.

---

## v1 scope — canonical forms this tool emits

The enumerator emits one canonical form per family. All listed strikes on the product's grid; no relative strikes, no synthetic strikes, no delta hedges in v1.

All strikes in the examples below are 2-decimal per the display rule above.

| Family | Canonical form | Example |
|---|---|---|
| Outright call | `{PRODEXP} {K} c` | `SFRZ6 97.00 c` |
| Outright put | `{PRODEXP} {K} p` | `SFRZ6 97.00 p` |
| Call spread | `{PRODEXP} {K1}/{K2} cs` | `SFRZ6 97.00/97.06 cs` |
| Put spread | `{PRODEXP} {K1}/{K2} ps` | `SFRZ6 97.00/96.93 ps` |
| Call fly | `{PRODEXP} {K1}/{K2}/{K3} c fly` | `SFRZ6 96.93/97.00/97.06 c fly` |
| Put fly | `{PRODEXP} {K1}/{K2}/{K3} p fly` | `SFRZ6 97.06/97.00/96.93 p fly` |
| Call condor | `{PRODEXP} {K1}/{K2}/{K3}/{K4} c condor` | `ERU6 97.81/97.87/97.93/98.06 c condor` |
| Put condor | `{PRODEXP} {K1}/{K2}/{K3}/{K4} p condor` | `ERU6 98.06/97.93/97.87/97.81 p condor` |
| Ratio call spread | `{PRODEXP} {K1}/{K2} {a}x{b} cs` | `SFRZ6 97.00/97.06 1x2 cs` |
| Ratio put spread | `{PRODEXP} {K1}/{K2} {a}x{b} ps` | `SFRZ6 97.00/96.93 1x2 ps` |
| Ratio call fly | `{PRODEXP} {K1}/{K2}/{K3} {a}x{b}x{c} cfly` | `ERQ6 97.81/98.00/98.18 1x3x2 cfly` |
| Ratio put fly | `{PRODEXP} {K1}/{K2}/{K3} {a}x{b}x{c} pfly` | `ERQ6 98.18/98.00/97.81 1x3x2 pfly` |
| Risk reversal | `{PRODEXP} {KP}/{KC} rr` | `SFRZ6 96.87/97.12 rr` |
| Straddle | `{PRODEXP} {K} ^` | `SFRZ6 97.00 ^` |
| Strangle | `{PRODEXP} {KP}/{KC} strangle` | `SFRZ6 96.87/97.12 strangle` |
| Calendar | `{PRODEXP1} {K} {c/p} vs {PRODEXP2} {K} {c/p}` | `SFRU6 97.00 c vs SFRZ6 97.00 c` |

**Product+expiry codes used by the enumerator:**

| Product | Code |
|---|---|
| SOFR (3m) whites | `SFR{M}{Y}` — e.g. `SFRZ6` |
| SOFR 1y mid-curve | `0Q{M}{Y}` |
| Euribor whites | `ER{M}{Y}` |
| Euribor mid-curve | `0R{M}{Y}` |
| SONIA whites | `SFI{M}{Y}` |
| SONIA mid-curve | `0N{M}{Y}` |

Month codes: H (Mar), M (Jun), U (Sep), Z (Dec). Year = single digit (6 = 2026).

**Strike grids and written form:**

| Product | Grid | Written as |
|---|---|---|
| SOFR (SR3, 0Q) | 6.25bp | 2-decimal truncation (e.g. `97.06`, `96.93`) |
| Euribor (ER, 0R) | 6.25bp | 2-decimal truncation (e.g. `97.06`, `96.81`) |
| SONIA (SFI, 0N) | 5bp | 2 decimals natively (e.g. `96.25`, `96.30`) |

**Display rule (all products):** strikes walk the product's grid internally but are written with 2 decimal places in the trade description. `97.0625` → `97.06`, `97.1875` → `97.18`, `96.8125` → `96.81`, `96.9375` → `96.93`. PM accepts both full-precision and truncated forms and resolves to the same listed strike; the desk uses 2-decimal for readability. The enumerator truncates at emit time.

PM validates strikes by rendering invalid rows in red. The enumerator snaps to the grid but does not re-implement PM's listed-strike validation.
