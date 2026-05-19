"""Tests for memory — cross-week save and load."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from kcp_structgen.analysis.memory import (
    MAX_HEADLINES,
    extract_headlines,
    load_prior_weeks,
    save_week_outputs,
)

# Reference week: 2026-W47 (week of 16-20 Nov 2026)
THIS_WEEK = date(2026, 11, 18)
PRIOR_W46_LABEL = "2026-W46"
PRIOR_W45_LABEL = "2026-W45"
PRIOR_W44_LABEL = "2026-W44"


# ---------------------------------------------------------------------------
# Headline extraction
# ---------------------------------------------------------------------------

def test_extract_headlines_basic():
    md = """\
## This week's headlines

- Paper rotated out of Z6 calls into puts on hot CPI
- M7 96.00 calls built steadily Mon-Fri (+15k)
- Quiet 0Q activity through the week

## OI themes
- Body text not in headlines
"""
    headlines = extract_headlines(md)
    assert headlines == [
        "Paper rotated out of Z6 calls into puts on hot CPI",
        "M7 96.00 calls built steadily Mon-Fri (+15k)",
        "Quiet 0Q activity through the week",
    ]


def test_extract_headlines_caps_at_max():
    bullets = "\n".join(f"- headline {i}" for i in range(10))
    md = f"## This week's headlines\n\n{bullets}\n\n## OI themes\n- other"
    headlines = extract_headlines(md)
    assert len(headlines) == MAX_HEADLINES


def test_extract_headlines_stops_at_next_h2():
    md = """\
## This week's headlines

- Inside the section

## Events

- This is in events, not headlines
"""
    assert extract_headlines(md) == ["Inside the section"]


def test_extract_headlines_no_section_returns_empty():
    md = "## OI themes\n- some bullet"
    assert extract_headlines(md) == []


def test_extract_headlines_empty_input():
    assert extract_headlines("") == []
    assert extract_headlines(None) == []


def test_extract_headlines_case_insensitive_heading():
    md = "## this week's HEADLINES\n\n- ok"
    assert extract_headlines(md) == ["ok"]


def test_extract_headlines_accepts_asterisk_bullets():
    md = "## This week's headlines\n\n* one\n* two"
    assert extract_headlines(md) == ["one", "two"]


def test_extract_headlines_ignores_non_bullets():
    md = """\
## This week's headlines

Some intro text without a bullet.

- An actual bullet
"""
    assert extract_headlines(md) == ["An actual bullet"]


# ---------------------------------------------------------------------------
# Save round-trip
# ---------------------------------------------------------------------------

def test_save_writes_three_files(tmp_path):
    digest = {"week": "2026-W47", "events": []}
    headlines = ["headline one", "headline two"]
    rundown = "## This week's headlines\n\n- headline one\n- headline two\n"
    paths = save_week_outputs(THIS_WEEK, digest, headlines, rundown,
                              memory_dir=tmp_path)
    assert paths["digest"].is_file()
    assert paths["headlines"].is_file()
    assert paths["rundown"].is_file()
    assert paths["digest"].name == "2026-W47.json"
    assert paths["headlines"].name == "2026-W47_headlines.md"
    assert paths["rundown"].name == "2026-W47_rundown.md"


def test_save_creates_memory_dir(tmp_path):
    nested = tmp_path / "new" / "nested" / "memory"
    save_week_outputs(THIS_WEEK, {"k": 1}, [], "rundown",
                      memory_dir=nested)
    assert nested.is_dir()


def test_save_overwrites_existing(tmp_path):
    """Re-running Friday after a correction must replace, not append."""
    save_week_outputs(THIS_WEEK, {"v": 1}, ["old"], "old rundown",
                      memory_dir=tmp_path)
    save_week_outputs(THIS_WEEK, {"v": 2}, ["new"], "new rundown",
                      memory_dir=tmp_path)
    digest = json.loads((tmp_path / "2026-W47.json").read_text(encoding="utf-8"))
    assert digest == {"v": 2}
    rundown = (tmp_path / "2026-W47_rundown.md").read_text(encoding="utf-8")
    assert rundown == "new rundown"


# ---------------------------------------------------------------------------
# load_prior_weeks
# ---------------------------------------------------------------------------

def _seed_week(memory_dir: Path, label: str, digest: dict,
               headlines: list[str] | None = None) -> None:
    (memory_dir / f"{label}.json").write_text(json.dumps(digest), encoding="utf-8")
    if headlines is not None:
        body = "\n".join(f"- {h}" for h in headlines)
        (memory_dir / f"{label}_headlines.md").write_text(
            f"# Headlines — {label}\n\n{body}\n", encoding="utf-8",
        )


def test_load_prior_weeks_empty_dir_returns_empty(tmp_path):
    assert load_prior_weeks(THIS_WEEK, n_weeks=3, memory_dir=tmp_path) == []


def test_load_prior_weeks_finds_one_prior(tmp_path):
    _seed_week(tmp_path, PRIOR_W46_LABEL, {"week": PRIOR_W46_LABEL})
    out = load_prior_weeks(THIS_WEEK, n_weeks=3, memory_dir=tmp_path)
    assert len(out) == 1
    assert out[0]["week"] == PRIOR_W46_LABEL
    assert out[0]["digest"]["week"] == PRIOR_W46_LABEL
    assert out[0]["headlines"] == []


def test_load_prior_weeks_three_priors_ordered_newest_first(tmp_path):
    _seed_week(tmp_path, PRIOR_W46_LABEL, {"week": PRIOR_W46_LABEL})
    _seed_week(tmp_path, PRIOR_W45_LABEL, {"week": PRIOR_W45_LABEL})
    _seed_week(tmp_path, PRIOR_W44_LABEL, {"week": PRIOR_W44_LABEL})
    out = load_prior_weeks(THIS_WEEK, n_weeks=3, memory_dir=tmp_path)
    assert [w["week"] for w in out] == [PRIOR_W46_LABEL, PRIOR_W45_LABEL, PRIOR_W44_LABEL]


def test_load_prior_weeks_skips_missing_in_middle(tmp_path):
    """If W45 file is missing, we still find W46 and W44 — no gap-failure."""
    _seed_week(tmp_path, PRIOR_W46_LABEL, {"week": PRIOR_W46_LABEL})
    _seed_week(tmp_path, PRIOR_W44_LABEL, {"week": PRIOR_W44_LABEL})
    out = load_prior_weeks(THIS_WEEK, n_weeks=3, memory_dir=tmp_path)
    assert [w["week"] for w in out] == [PRIOR_W46_LABEL, PRIOR_W44_LABEL]


def test_load_prior_weeks_headlines_loaded_when_present(tmp_path):
    _seed_week(tmp_path, PRIOR_W46_LABEL, {"week": PRIOR_W46_LABEL},
               headlines=["theme one", "theme two"])
    out = load_prior_weeks(THIS_WEEK, n_weeks=1, memory_dir=tmp_path)
    assert out[0]["headlines"] == ["theme one", "theme two"]


def test_load_prior_weeks_handles_corrupt_digest(tmp_path):
    """A malformed JSON file is skipped, not propagated as an exception."""
    (tmp_path / f"{PRIOR_W46_LABEL}.json").write_text("not valid json {{",
                                                       encoding="utf-8")
    _seed_week(tmp_path, PRIOR_W45_LABEL, {"week": PRIOR_W45_LABEL})
    out = load_prior_weeks(THIS_WEEK, n_weeks=2, memory_dir=tmp_path)
    assert [w["week"] for w in out] == [PRIOR_W45_LABEL]


def test_load_prior_weeks_respects_n_weeks_cap(tmp_path):
    """n_weeks=2 should not look further back even if more files exist."""
    _seed_week(tmp_path, PRIOR_W46_LABEL, {"week": PRIOR_W46_LABEL})
    _seed_week(tmp_path, PRIOR_W45_LABEL, {"week": PRIOR_W45_LABEL})
    _seed_week(tmp_path, PRIOR_W44_LABEL, {"week": PRIOR_W44_LABEL})
    out = load_prior_weeks(THIS_WEEK, n_weeks=2, memory_dir=tmp_path)
    assert len(out) == 2
    assert PRIOR_W44_LABEL not in [w["week"] for w in out]


# ---------------------------------------------------------------------------
# Save then load — full integration
# ---------------------------------------------------------------------------

def test_save_then_load_roundtrip(tmp_path):
    digest_w46 = {"week": PRIOR_W46_LABEL, "products": ["SR3"]}
    headlines_w46 = ["build in Z6 calls", "M7 unwind"]
    rundown_w46 = ("## This week's headlines\n\n"
                   "- build in Z6 calls\n"
                   "- M7 unwind\n\n"
                   "## Events\n- CPI was hot\n")

    # Save week 46
    save_week_outputs(date(2026, 11, 11), digest_w46, headlines_w46, rundown_w46,
                      memory_dir=tmp_path)

    # Load from this week's perspective
    priors = load_prior_weeks(THIS_WEEK, n_weeks=1, memory_dir=tmp_path)
    assert len(priors) == 1
    assert priors[0]["week"] == PRIOR_W46_LABEL
    assert priors[0]["digest"] == digest_w46
    assert priors[0]["headlines"] == headlines_w46
