"""``session_persistence`` on :class:`ClaudeCliLLM` — ``--no-session-persistence``."""

from __future__ import annotations

import json
from typing import Any

import pytest

from litellm_claude_cli import Capabilities, ClaudeCliLLM


def _argv_for(**kwargs: Any) -> list[str]:
    captured: list[list[str]] = []

    def _runner(argv: list[str], *, input_text: str | None, timeout: float) -> str:
        captured.append(argv)
        return json.dumps(
            {"is_error": False, "result": "ok", "stop_reason": "end_turn", "usage": {}}
        )

    llm = ClaudeCliLLM(runner=_runner, **kwargs)
    llm.completion(
        model="claude-cli/haiku", messages=[{"role": "user", "content": "hi"}]
    )
    return captured[0]


def test_default_persists() -> None:
    assert "--no-session-persistence" not in _argv_for()


def test_true_persists() -> None:
    assert "--no-session-persistence" not in _argv_for(session_persistence=True)


def test_false_emits_flag_once() -> None:
    argv = _argv_for(
        session_persistence=False,
        setting_sources=(),
        capabilities=Capabilities(exclusive=True),
    )
    assert argv.count("--no-session-persistence") == 1


@pytest.mark.parametrize("bad", [0, 1, None, "false"])
def test_non_bool_raises(bad: Any) -> None:
    with pytest.raises(ValueError, match="session_persistence"):
        ClaudeCliLLM(session_persistence=bad)
