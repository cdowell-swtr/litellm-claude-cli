"""``setting_sources`` on :class:`ClaudeCliLLM` — emitted as ``--setting-sources``."""

from __future__ import annotations

import json
from typing import Any

import pytest

from litellm_claude_cli import Capabilities, ClaudeCliLLM


def _ok() -> str:
    return json.dumps(
        {
            "is_error": False,
            "result": "ok",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def _argv_for(**kwargs: Any) -> list[str]:
    captured: list[list[str]] = []

    def _runner(argv: list[str], *, input_text: str | None, timeout: float) -> str:
        captured.append(argv)
        return _ok()

    llm = ClaudeCliLLM(runner=_runner, **kwargs)
    llm.completion(
        model="claude-cli/haiku", messages=[{"role": "user", "content": "hi"}]
    )
    return captured[0]


def _flag_value(argv: list[str]) -> str:
    assert argv.count("--setting-sources") == 1
    return argv[argv.index("--setting-sources") + 1]


def test_absent_emits_no_flag() -> None:
    assert "--setting-sources" not in _argv_for()


def test_empty_tuple_loads_no_sources() -> None:
    assert _flag_value(_argv_for(setting_sources=())) == ""


def test_sources_joined_in_caller_order() -> None:
    argv = _argv_for(setting_sources=("project", "user"))
    assert _flag_value(argv) == "project,user"


def test_combines_with_exclusive() -> None:
    argv = _argv_for(
        setting_sources=("project",), capabilities=Capabilities(exclusive=True)
    )
    assert _flag_value(argv) == "project"
    assert "--strict-mcp-config" in argv


def test_list_is_accepted_and_copied() -> None:
    sources = ["local"]
    llm = ClaudeCliLLM(setting_sources=sources)
    sources.append("user")
    assert llm._setting_sources == ("local",)


@pytest.mark.parametrize("bad", [("User",), ("managed",), ("project", "")])
def test_unknown_source_raises_at_construction(bad: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="setting source"):
        ClaudeCliLLM(setting_sources=bad)


def test_bare_string_raises() -> None:
    # A str is iterable, so "user" would otherwise validate as "u","s","e","r"
    # and fail confusingly — or, for "local"-shaped typos, not at all.
    with pytest.raises(ValueError, match="sequence"):
        ClaudeCliLLM(setting_sources="project")  # type: ignore[arg-type]
