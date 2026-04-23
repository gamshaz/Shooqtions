"""NL scenario parser.

Shells out to `claude -p --output-format json` using the system prompt in
`prompts/parse_scenario.md`. Returns a dict matching the schema documented
in spec.md §2.2. Fails loud on subprocess errors, timeouts, and invalid
JSON — no silent fallbacks.

Deliberately tiny. The LLM does extraction, the enumerator does everything
else. See CLAUDE.md for the responsibility split.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "parse_scenario.md"
CLAUDE_CLI = "claude"
SUBPROCESS_TIMEOUT_S = 120


class ParserError(RuntimeError):
    """Any failure in the parse pipeline. Wraps the underlying cause."""


def _load_system_prompt() -> str:
    if not SYSTEM_PROMPT_PATH.is_file():
        raise ParserError(f"system prompt missing at {SYSTEM_PROMPT_PATH}")
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _check_claude_available() -> None:
    if shutil.which(CLAUDE_CLI) is None:
        raise ParserError(
            f"`{CLAUDE_CLI}` CLI not on PATH. Install Claude Code under your "
            "Enterprise seat and retry."
        )


def parse_scenario(text: str) -> dict:
    """Parse a natural-language scenario into structured params.

    Returns a dict matching spec.md §2.2. Raises ParserError on any failure
    (CLI missing, timeout, non-zero exit, invalid JSON).
    """
    if not text or not text.strip():
        raise ParserError("empty scenario")

    _check_claude_available()
    system_prompt = _load_system_prompt()

    try:
        result = subprocess.run(
            [CLAUDE_CLI, "-p", "--output-format", "json", "--append-system-prompt", system_prompt],
            input=text,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired as exc:
        raise ParserError(f"claude -p timed out after {SUBPROCESS_TIMEOUT_S}s — check your network or Claude Code login") from exc
    except FileNotFoundError as exc:
        raise ParserError(f"could not execute `{CLAUDE_CLI}`: {exc}") from exc

    if result.returncode != 0:
        raise ParserError(
            f"claude -p exited {result.returncode}. stderr:\n{result.stderr.strip()}"
        )

    # `claude -p --output-format json` wraps the model's response in its own
    # JSON envelope. We parse that, pull the model's text, then parse the
    # model's text as the params JSON.
    try:
        envelope = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ParserError(f"claude -p returned non-JSON stdout:\n{result.stdout}") from exc

    model_text = envelope.get("result") if isinstance(envelope, dict) else None
    if not isinstance(model_text, str):
        raise ParserError(f"unexpected claude -p envelope shape:\n{envelope!r}")

    # Strip markdown code fences the model sometimes wraps around JSON.
    cleaned = model_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0].strip()

    try:
        params = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ParserError(
            f"model output was not valid JSON:\n{model_text}"
        ) from exc

    if not isinstance(params, dict):
        raise ParserError(f"model output was JSON but not an object: {params!r}")

    return params
