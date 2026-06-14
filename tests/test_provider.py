"""Tests for the LiteLLM CustomLLM provider that wraps `claude -p`.

TDD: these tests were written before the implementation.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import litellm
import pytest

from litellm_claude_cli import (
    ClaudeCliLLM,
    ClaudeExhausted,
    _build_response,
    _exhaustion_error,
    _render_messages_to_prompt,
    register,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_json_response(
    result: str = "[]",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    stop_reason: str = "end_turn",
) -> str:
    return json.dumps(
        {
            "is_error": False,
            "result": result,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read_input_tokens,
                "cache_creation_input_tokens": cache_creation_input_tokens,
            },
        }
    )


def _make_llm_with_response(raw_response: str) -> tuple[ClaudeCliLLM, dict[str, Any]]:
    """Return a ClaudeCliLLM whose runner returns raw_response and captures call args."""
    captured: dict[str, Any] = {"argv": None, "input_text": None}

    def _runner(argv: list[str], *, input_text: str | None) -> str:
        captured["argv"] = argv
        captured["input_text"] = input_text
        return raw_response

    return ClaudeCliLLM(runner=_runner), captured


# ---------------------------------------------------------------------------
# Required test 1: system goes via file, user prompt via stdin
# ---------------------------------------------------------------------------


def test_handler_single_turn_system_via_file_prompt_via_stdin() -> None:
    """System content written to a temp file; user prompt passed via stdin."""
    raw = _fake_json_response(result="found nothing interesting")
    file_content_read: dict[str, str] = {}

    def _capturing_runner(argv: list[str], *, input_text: str | None) -> str:
        # Read the system-prompt-file while it still exists
        idx = argv.index("--system-prompt-file") + 1
        with open(argv[idx]) as fh:
            file_content_read["content"] = fh.read()
        file_content_read["argv"] = argv  # type: ignore[assignment]
        file_content_read["input_text"] = input_text
        return raw

    llm = ClaudeCliLLM(runner=_capturing_runner)
    messages = [
        {"role": "system", "content": "SYS-A"},
        {"role": "user", "content": "Return your findings as a JSON array only."},
    ]
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=messages,
        optional_params={},
    )

    argv = file_content_read["argv"]
    assert isinstance(argv, list)

    # System must go via file, NOT inline
    assert "--system-prompt-file" in argv, "--system-prompt-file not in argv"
    assert "--system-prompt" not in argv, "--system-prompt (inline) was found in argv"

    # File content must equal the system text
    assert file_content_read["content"] == "SYS-A"

    # User text must go via stdin
    assert (
        file_content_read["input_text"] == "Return your findings as a JSON array only."
    )

    # Output format
    assert "--output-format" in argv
    assert "json" in argv

    # Model stripped of prefix
    assert "--model" in argv
    model_idx = argv.index("--model") + 1
    assert argv[model_idx] == "claude-haiku-4-5-20251001", (
        f"prefix not stripped: {argv[model_idx]}"
    )

    # Disallowed tools
    assert "--disallowed-tools" in argv

    # Response text
    assert resp.choices[0].message.content == "found nothing interesting"


# ---------------------------------------------------------------------------
# Required test 2: large system never appears in argv
# ---------------------------------------------------------------------------


def test_large_system_never_appears_as_argv() -> None:
    """A 200k-char system prompt must never appear as an argv element."""
    big_system = "x" * 200_000
    file_content_read: dict[str, Any] = {}

    def _capturing_runner(argv: list[str], *, input_text: str | None) -> str:
        idx = argv.index("--system-prompt-file") + 1
        with open(argv[idx]) as fh:
            file_content_read["content"] = fh.read()
        file_content_read["argv"] = argv
        file_content_read["input_text"] = input_text
        return _fake_json_response(result="ok")

    llm = ClaudeCliLLM(runner=_capturing_runner)
    messages = [
        {"role": "system", "content": big_system},
        {"role": "user", "content": "What do you see?"},
    ]
    llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=messages,
        optional_params={},
    )

    argv = file_content_read["argv"]
    # No argv element exceeds Linux MAX_ARG_STRLEN (128 KB)
    max_len = max(len(a) for a in argv)
    assert max_len <= 131072, f"argv element too long: {max_len}"

    # System goes via file
    assert "--system-prompt-file" in argv
    assert file_content_read["content"] == big_system

    # User goes via stdin
    assert file_content_read["input_text"] == "What do you see?"


# ---------------------------------------------------------------------------
# Required test 3: session-limit error raises ClaudeExhausted with hint
# ---------------------------------------------------------------------------


def test_session_limit_raises_claude_exhausted_with_hint() -> None:
    """is_error response with session limit marker raises ClaudeExhausted with reset_hint."""
    error_payload = json.dumps(
        {
            "is_error": True,
            "api_error_status": 429,
            "result": "You've hit your session limit · resets 11:30am (America/Los_Angeles)",
        }
    )

    llm, _ = _make_llm_with_response(error_payload)
    with pytest.raises(ClaudeExhausted) as exc_info:
        llm.completion(
            model="claude-cli/claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hello"}],
            optional_params={},
        )

    assert "11:30" in (exc_info.value.reset_hint or ""), (
        f"hint missing '11:30': {exc_info.value.reset_hint!r}"
    )


# ---------------------------------------------------------------------------
# Additional tests
# ---------------------------------------------------------------------------


def test_non_json_output_raises_runtime_error() -> None:
    """Non-JSON output from the runner raises RuntimeError."""
    llm, _ = _make_llm_with_response("not-json-at-all")
    with pytest.raises(RuntimeError, match="non-JSON"):
        llm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "hi"}],
            optional_params={},
        )


def test_usage_cache_fields_populated() -> None:
    """Cache token fields are attached to the response usage."""
    raw = _fake_json_response(
        result="cached result",
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=80,
        cache_creation_input_tokens=5,
    )
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "x"}],
        optional_params={},
    )
    usage = resp.usage
    assert usage is not None
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 20
    assert usage.cache_read_input_tokens == 80
    assert usage.cache_creation_input_tokens == 5


def test_prefix_stripped_from_model() -> None:
    """The `claude-cli/` prefix is stripped before forwarding to --model."""
    captured: dict[str, Any] = {}

    def _runner(argv: list[str], *, input_text: str | None) -> str:
        captured["argv"] = argv
        # Read the system file to prevent file-not-found after unlink
        idx = argv.index("--system-prompt-file") + 1
        with open(argv[idx]) as fh:
            fh.read()
        return _fake_json_response()

    llm = ClaudeCliLLM(runner=_runner)
    llm.completion(
        model="claude-cli/claude-opus-4-8",
        messages=[{"role": "user", "content": "y"}],
        optional_params={},
    )
    argv = captured["argv"]
    model_idx = argv.index("--model") + 1
    assert argv[model_idx] == "claude-opus-4-8"


def test_render_messages_system_and_user() -> None:
    """_render_messages_to_prompt extracts system text; user turn passed as raw text."""
    messages = [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "Hello there."},
    ]
    system_text, user_text = _render_messages_to_prompt(messages)
    assert system_text == "Be helpful."
    # User content arrives without a [user] prefix so it goes cleanly to stdin.
    assert user_text == "Hello there."


def test_render_messages_tool_result() -> None:
    """Tool-result messages are rendered as [tool_result]."""
    messages = [
        {"role": "tool", "content": "the file content"},
    ]
    _, prompt = _render_messages_to_prompt(messages)
    assert "[tool_result]" in prompt
    assert "the file content" in prompt


def test_render_messages_assistant_tool_call() -> None:
    """Assistant messages with tool_calls are rendered with name and arguments."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "function": {
                        "name": "Read",
                        "arguments": '{"path": "/tmp/x"}',
                    }
                }
            ],
            "content": "",
        }
    ]
    _, prompt = _render_messages_to_prompt(messages)
    assert "[assistant tool_call]" in prompt
    assert "Read" in prompt


def test_render_messages_content_blocks() -> None:
    """Content can be a list of text blocks."""
    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": "sys block",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "user block"}],
        },
    ]
    system_text, user_text = _render_messages_to_prompt(messages)
    assert system_text == "sys block"
    assert "user block" in user_text


def test_exhaustion_error_no_match_returns_none() -> None:
    """_exhaustion_error returns None for non-exhaustion text."""
    assert _exhaustion_error("all fine here") is None


def test_exhaustion_error_with_reset_hint() -> None:
    """_exhaustion_error extracts the reset hint from the text."""
    err = _exhaustion_error("You've hit your usage limit. resets 3pm PST. Try later.")
    assert err is not None
    assert isinstance(err, ClaudeExhausted)
    assert err.reset_hint is not None
    assert "3pm" in err.reset_hint


def test_build_response_error_no_exhaustion_raises_runtime() -> None:
    """is_error without exhaustion markers raises RuntimeError."""
    raw = json.dumps({"is_error": True, "result": "some other error occurred"})
    with pytest.raises(RuntimeError, match="claude -p error"):
        _build_response(raw)


def test_build_response_non_dict_raises_runtime() -> None:
    """JSON that isn't a dict raises RuntimeError."""
    with pytest.raises(RuntimeError):
        _build_response(json.dumps([1, 2, 3]))


def test_acompletion_delegates_to_run() -> None:
    """acompletion delegates to the same _run path as completion."""
    import asyncio

    raw = _fake_json_response(result="async-result")
    llm, _ = _make_llm_with_response(raw)
    result = asyncio.run(
        llm.acompletion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "async call"}],
            optional_params={},
        )
    )
    assert result.choices[0].message.content == "async-result"


def test_register_adds_provider_to_custom_provider_map() -> None:
    """register() adds claude-cli to litellm.custom_provider_map idempotently."""
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = []
    try:
        register()
        assert any(
            p.get("provider") == "claude-cli" for p in litellm.custom_provider_map
        )
        # idempotent: calling again should not add a duplicate
        register()
        count = sum(
            1 for p in litellm.custom_provider_map if p.get("provider") == "claude-cli"
        )
        assert count == 1
    finally:
        litellm.custom_provider_map = saved


def test_register_replaces_existing_claude_cli_entry() -> None:
    """register() replaces any existing claude-cli entry rather than duplicating."""
    saved = litellm.custom_provider_map
    old_handler = MagicMock()
    litellm.custom_provider_map = [
        {"provider": "claude-cli", "custom_handler": old_handler}
    ]
    try:
        register()
        entries = [
            p for p in litellm.custom_provider_map if p.get("provider") == "claude-cli"
        ]
        assert len(entries) == 1
        assert entries[0]["custom_handler"] is not old_handler
    finally:
        litellm.custom_provider_map = saved
