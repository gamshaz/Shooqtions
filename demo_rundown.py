"""Standalone DEMO rundown generator — NOT the real Layer 2 pipeline.

One-shot: read the week's CME OI files + flow Excel + ITC commentary docs,
dump everything (lightly structured) into a single `claude -p` call with an
analyst prompt, save the markdown result.

This deliberately bypasses the real pipeline (segmenter, events API, FOMC
scraper, aggregator, memory). The LLM does all synthesis itself. Good enough
for a demo; the proper pipeline stays for production.

Reuses cme_loader for the .xls parsing only (that layout is gnarly and
already solved). Everything else is inline.

Run:  python demo_rundown.py
Out:  demo_rundown_output.md
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

# Make src importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from kcp_structgen.analysis.cme_loader import load_cme_voi  # noqa: E402
from kcp_structgen.analysis.events_api import load_events_for_week  # noqa: E402
from kcp_structgen.analysis.event_matcher import tag_events  # noqa: E402
from kcp_structgen.analysis.classifier import classify_events  # noqa: E402

try:
    from docx import Document
except ImportError:
    print("python-docx not installed. Run: pip install python-docx")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("pandas not installed. Run: pip install pandas openpyxl xlrd")
    sys.exit(1)

DATA = Path(__file__).resolve().parent / "data"
OUT_PATH = Path(__file__).resolve().parent / "demo_rundown_output.md"
CLAUDE_CLI = "claude"
CLAUDE_TIMEOUT_S = 240


SYSTEM_PROMPT = """\
You are a rates strategist writing the weekly positioning note for a STIR
options sales desk. The desk trades SOFR (SR3) and 1-year SOFR mid-curve (0Q)
options. Your readers are rates traders who already know the market.

You receive, for one trading week:
  - The US economic calendar for the week, with consensus, actual, and a
    hot/cold/inline surprise tag per release.
  - Daily CME open-interest data per strike (At Close = OI, Change = day's
    ΔOI, plus volume), per expiry, calls and puts, for SR3 and 0Q.
  - Street flow intel the desk logged (what paper was reportedly doing).
  - Daily ITC US Rates morning commentary (headlines + market colour).

Use the economic calendar to anchor the WHY: when positioning shifted around
a data print or FOMC event, say so and cite the surprise (e.g. "after CPI
printed hot at 3.2 vs 2.9"). Hot inflation/jobs data is bearish for rates
(price down); cold is bullish.

Write a markdown note with these sections, in this order:

## This week's headlines
3-5 bullets: the most important positioning shifts of the week.

## OI themes
Where open interest built or unwound. Cite specific strikes, expiries, and
the ΔOI numbers. Note concentration and any call/put skew in the activity.

## Flow & commentary
What paper was reportedly doing (from the flow log) and how the ITC
commentary explains the backdrop. Tie flow to OI moves where they line up.
Use the commentary headlines to explain WHY positioning shifted.

## Watch for next week
2-3 forward-looking bullets.

Rules:
- Cite real numbers from the data. Name strikes as e.g. "SFRZ6 96.75c".
- Every observation should have a what (the data) and, where the commentary
  supports it, a why.
- Be sharp but don't overreach beyond the evidence. Thin signal, thin
  language.
- No trade recommendations — describe positioning, don't advise.
- Markdown only. Start with `## This week's headlines`. No preamble, no
  fences.
"""


def _money(n):
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def load_oi() -> dict[str, dict]:
    """Parse all CME .xls files in data/oi/daily/ via the real loader."""
    out: dict[str, dict] = {}
    for xls in sorted(glob.glob(str(DATA / "oi" / "daily" / "*.xls"))):
        stem = Path(xls).stem  # 'YYYY-MM-DD'
        try:
            d = date.fromisoformat(stem)
            out[stem] = load_cme_voi(xls, trade_date=d)
            print(f"  loaded OI {stem}")
        except Exception as exc:
            print(f"  SKIP {stem}: {type(exc).__name__}: {exc}")
    return out


def summarise_oi_for_prompt(oi: dict[str, dict]) -> str:
    """Condense the per-day digests into a compact text block: top OI
    changes per day across SR3 + 0Q, so the prompt isn't 10k strike rows."""
    lines: list[str] = []
    for day in sorted(oi):
        digest = oi[day]
        lines.append(f"\n### {day}")
        rows = []
        for product, expiries in digest.get("options", {}).items():
            for expiry, sides in expiries.items():
                for side_key, cp in (("calls", "c"), ("puts", "p")):
                    for r in sides.get(side_key, []):
                        chg = r.get("oi_change", 0) or 0
                        if chg == 0 and (r.get("volume", 0) or 0) == 0:
                            continue
                        rows.append((
                            abs(chg), product, expiry, r["strike"], cp,
                            chg, r.get("oi", 0), r.get("volume", 0),
                        ))
        rows.sort(reverse=True)
        if not rows:
            lines.append("  (no strike activity)")
        for _absc, product, expiry, strike, cp, chg, oi_close, vol in rows[:15]:
            lines.append(
                f"  {product}{expiry} {strike:.2f}{cp}  "
                f"dOI={_money(chg):>9}  OI={_money(oi_close):>9}  "
                f"vol={_money(vol):>8}"
            )
    return "\n".join(lines)


def load_events(oi: dict[str, dict]) -> str:
    """Pull the US economic calendar for the week of the OI data from FMP.

    Derives the target week from the earliest OI date. Tags + classifies
    events (hot/cold/inline). Degrades to a note if no FMP key / network.
    """
    if not oi:
        return "(no OI dates to derive a week from)"
    first_day = date.fromisoformat(sorted(oi)[0])
    try:
        events = load_events_for_week(first_day)
    except Exception as exc:
        print(f"  events fetch failed: {type(exc).__name__}: {exc}")
        return f"(economic calendar unavailable: {exc})"
    if not events:
        print("  no events returned (FMP key not set or no US events that week)")
        return "(no economic calendar data — set FMP key in config/settings.json)"

    tag_events(events)
    classify_events(events)
    print(f"  loaded {len(events)} economic events")

    lines = []
    for ev in sorted(events, key=lambda e: e.get("date", "")):
        d = ev.get("date", "?")
        name = ev.get("event_name", "?")
        prev = ev.get("previous")
        est = ev.get("estimate")
        act = ev.get("actual")
        surprise = ev.get("surprise")
        bits = [f"{d}", name]
        if est is not None:
            bits.append(f"est={est}")
        if act is not None:
            bits.append(f"actual={act}")
        if prev is not None:
            bits.append(f"prev={prev}")
        if surprise:
            bits.append(f"[{surprise.upper()}]")
        lines.append("  " + "  ".join(str(b) for b in bits))
    return "\n".join(lines)


def load_flow() -> str:
    path = DATA / "flow" / "flow.xlsx"
    if not path.is_file():
        return "(no flow file)"
    try:
        df = pd.read_excel(path)
    except Exception as exc:
        return f"(flow unreadable: {exc})"
    df = df.dropna(how="all")
    if df.empty:
        return "(flow file empty)"
    # Just hand the LLM the rows as text.
    return df.to_string(index=False)


def _docx_all_text(path: str) -> str:
    """Extract text from a docx by walking every <w:t> text node.

    These ITC docs are emails pasted into Word as deeply-nested HTML tables,
    so `.paragraphs` and even `.cells` come back empty — the text lives in
    raw `w:t` nodes. Iterating them directly is the only reliable extraction.
    Newlines are inserted at paragraph (`w:p`) boundaries so it stays
    readable.
    """
    import re
    from docx.oxml.ns import qn
    doc = Document(path)
    body = doc.element.body
    parts: list[str] = []
    for node in body.iter():
        tag = node.tag
        if tag == qn("w:t") and node.text:
            parts.append(node.text)
        elif tag == qn("w:p"):
            parts.append("\n")
        elif tag in (qn("w:br"), qn("w:tab")):
            parts.append(" ")
    text = "".join(parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    return text.strip()


def load_commentary() -> str:
    raw = DATA / "commentary" / "raw"
    blocks: list[str] = []
    # Accept both flat <date>.docx and <date>/itc*.docx layouts.
    candidates = sorted(glob.glob(str(raw / "*.docx"))) + \
        sorted(glob.glob(str(raw / "*" / "*.docx")))
    for f in candidates:
        b = os.path.basename(f)
        if b.startswith("~"):  # Word lock file
            continue
        try:
            txt = _docx_all_text(f)
        except Exception as exc:
            print(f"  SKIP commentary {b}: {exc}")
            continue
        if txt.strip():
            label = Path(f).stem
            # Full document — no cap. Modern Claude handles the context fine.
            blocks.append(f"\n### ITC commentary — {label}\n{txt}")
            print(f"  loaded commentary {b} ({len(txt)} chars, full)")
    return "\n".join(blocks) if blocks else "(no commentary)"


def build_user_input(oi_text, flow_text, commentary_text, events_text) -> str:
    return (
        "# WEEK DATA\n\n"
        "## US ECONOMIC CALENDAR (this week, with consensus/actual/surprise)\n"
        f"{events_text}\n\n"
        "## CME OPEN INTEREST (top strike activity per day)\n"
        f"{oi_text}\n\n"
        "## STREET FLOW LOG\n"
        f"{flow_text}\n\n"
        "## DAILY ITC COMMENTARY\n"
        f"{commentary_text}\n"
    )


def call_claude(user_input: str) -> str:
    import shutil
    if shutil.which(CLAUDE_CLI) is None:
        print("ERROR: `claude` CLI not on PATH. Log in to Claude Code first.")
        sys.exit(1)
    print("\nCalling claude -p (this may take 30-120s)...")
    result = subprocess.run(
        [CLAUDE_CLI, "-p", "--append-system-prompt", SYSTEM_PROMPT],
        input=user_input,
        capture_output=True,
        text=True,
        timeout=CLAUDE_TIMEOUT_S,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"claude -p exited {result.returncode}")
        print("stderr:", (result.stderr or "")[:1000])
        sys.exit(1)
    out = (result.stdout or "").strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return out


def main():
    print("Loading data...")
    oi = load_oi()
    oi_text = summarise_oi_for_prompt(oi)
    events_text = load_events(oi)
    flow_text = load_flow()
    commentary_text = load_commentary()

    user_input = build_user_input(oi_text, flow_text, commentary_text, events_text)

    # Save the prompt input too, so you can see what the LLM saw.
    (Path(__file__).resolve().parent / "demo_prompt_input.txt").write_text(
        user_input, encoding="utf-8")

    rundown = call_claude(user_input)
    OUT_PATH.write_text(rundown, encoding="utf-8")
    print(f"\nDone. Rundown saved to {OUT_PATH.name}")
    print(f"Prompt input saved to demo_prompt_input.txt")
    print("\n" + "=" * 60)
    print(rundown[:1500])
    if len(rundown) > 1500:
        print("\n... (truncated; full text in the .md file)")


if __name__ == "__main__":
    main()
