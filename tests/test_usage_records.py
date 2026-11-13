"""Per-call usage records written to ``LITELLM_CLAUDE_CLI_USAGE_DIR`` (LCC9)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

import litellm_claude_cli
from litellm_claude_cli import ClaudeCliLLM

_KEYS = {
    "t",
    "model",
    "input",
    "cache_write",
    "cache_read",
    "output",
    "account",
    "caller",
}


def _llm(payload: dict[str, Any] | None = None) -> ClaudeCliLLM:
    raw = json.dumps(
        payload
        or {
            "is_error": False,
            "result": "secret completion text",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 5,
            },
        }
    )

    def _runner(argv: list[str], *, input_text: str | None, timeout: float) -> str:
        return raw

    return ClaudeCliLLM(runner=_runner)


def _call(llm: ClaudeCliLLM) -> None:
    llm.completion(
        model="claude-cli/sonnet",
        messages=[{"role": "user", "content": "secret prompt text"}],
    )


def _lines(directory: Path) -> list[dict[str, Any]]:
    files = list(directory.glob("*.jsonl"))
    assert len(files) == 1, files
    text = files[0].read_text()
    assert text.endswith("\n")
    return [json.loads(line) for line in text.splitlines()]


@pytest.fixture(autouse=True)
def _fresh_log(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each test starts as a process that has not yet opened a usage file.
    monkeypatch.setattr(
        litellm_claude_cli, "_usage_log", litellm_claude_cli._UsageLog()
    )


def test_call_with_account_writes_one_line_with_every_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_ACCOUNT", "claude/acme")

    _call(_llm())

    [line] = _lines(tmp_path)
    assert set(line) == _KEYS
    assert line["account"] == "claude/acme"
    assert line["model"] == "sonnet"
    assert (line["input"], line["cache_write"], line["cache_read"], line["output"]) == (
        11,
        5,
        3,
        7,
    )
    assert isinstance(line["t"], float) and line["t"] > 0
    assert line["caller"] is None or isinstance(line["caller"], str)
    text = next(tmp_path.glob("*.jsonl")).read_text()
    assert "secret" not in text


def test_second_call_without_account_appends_null_to_the_same_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_ACCOUNT", "claude/acme")
    llm = _llm()
    _call(llm)
    monkeypatch.setenv("AGENT_ACCOUNT", "")

    _call(llm)

    first, second = _lines(tmp_path)
    assert first["account"] == "claude/acme"
    assert second["account"] is None
    monkeypatch.delenv("AGENT_ACCOUNT")
    _call(llm)
    assert _lines(tmp_path)[2]["account"] is None


def test_unset_dir_writes_nothing_and_the_call_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LITELLM_CLAUDE_CLI_USAGE_DIR", raising=False)
    result = _llm().completion(
        model="claude-cli/sonnet", messages=[{"role": "user", "content": "hi"}]
    )
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", "")
    _call(_llm())

    assert result.choices[0].message.content == "secret completion text"
    assert list(tmp_path.iterdir()) == []


def test_each_model_usage_entry_gets_its_own_line_under_its_resolved_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_ACCOUNT", "claude/acme")
    payload = {
        "is_error": False,
        "result": "x",
        "usage": {"input_tokens": 999, "output_tokens": 999},
        "modelUsage": {
            "claude-sonnet-5-5": {
                "inputTokens": 1,
                "outputTokens": 2,
                "cacheCreationInputTokens": 4,
                "cacheReadInputTokens": 3,
            },
            "claude-haiku-5-5": {"inputTokens": 10, "outputTokens": 20},
        },
    }
    _call(_llm(payload))

    first, second = _lines(tmp_path)
    assert set(first) == set(second) == _KEYS
    counts = {
        line["model"]: (
            line["input"],
            line["cache_write"],
            line["cache_read"],
            line["output"],
        )
        for line in (first, second)
    }
    assert counts == {
        "claude-sonnet-5-5": (1, 4, 3, 2),
        "claude-haiku-5-5": (10, 0, 0, 20),
    }
    for key in ("t", "account", "caller"):
        assert first[key] == second[key]
    assert first["account"] == "claude/acme"


def test_without_model_usage_one_line_names_the_requested_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", str(tmp_path))

    _call(_llm())

    [line] = _lines(tmp_path)
    assert line["model"] == "sonnet"
    assert (line["input"], line["cache_write"], line["cache_read"], line["output"]) == (
        11,
        5,
        3,
        7,
    )


def test_write_failure_does_not_fail_the_call_and_logs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", str(blocker))
    llm = _llm()

    with caplog.at_level(logging.WARNING, logger="litellm_claude_cli"):
        _call(llm)
        _call(llm)

    warnings = [r for r in caplog.records if r.name == "litellm_claude_cli"]
    assert len(warnings) == 1


def test_a_forked_process_opens_its_own_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LITELLM_CLAUDE_CLI_USAGE_DIR", str(tmp_path))
    llm = _llm()
    _call(llm)
    real_pid = litellm_claude_cli.os.getpid()
    monkeypatch.setattr(litellm_claude_cli.os, "getpid", lambda: real_pid + 1)

    _call(llm)

    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 2
    assert all(len(f.read_text().splitlines()) == 1 for f in files)
