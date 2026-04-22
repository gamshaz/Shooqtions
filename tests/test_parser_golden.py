"""Golden tests for the NL parser.

Each file in tests/golden/*.json is loaded, run through parse_scenario(),
and compared against its expected params. Tests are skipped if the `claude`
CLI is not on PATH (so `pytest` works locally even without Claude Code
installed), but they MUST pass in CI and before any prompt change ships.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from kcp_structgen.parser import CLAUDE_CLI, parse_scenario

GOLDEN_DIR = Path(__file__).parent / "golden"


def _golden_files() -> list[Path]:
    return sorted(p for p in GOLDEN_DIR.glob("*.json"))


if shutil.which(CLAUDE_CLI) is None:
    pytest.skip(f"`{CLAUDE_CLI}` CLI not on PATH — skipping golden tests",
                allow_module_level=True)


@pytest.mark.parametrize("path", _golden_files(), ids=lambda p: p.stem)
def test_golden_scenario(path: Path):
    case = json.loads(path.read_text(encoding="utf-8"))
    scenario = case["scenario"]
    expected = case["expected"]

    got = parse_scenario(scenario)

    # Compare field-by-field for a clear failure message.
    for key, want in expected.items():
        assert got.get(key) == want, (
            f"{path.name}: field {key!r} mismatch\n"
            f"  expected: {want!r}\n"
            f"  got:      {got.get(key)!r}\n"
            f"  full got: {got!r}"
        )
