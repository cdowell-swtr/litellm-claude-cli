"""Proves the provider actually plugs into litellm: anthropic_messages(model="claude-cli/...")
must dispatch to ClaudeCliLLM and round-trip a well-formed response. Fully offline
(fake subprocess runner — no network, no key, no real `claude`)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import litellm
import pytest
from litellm_claude_cli import ClaudeCliLLM, ClaudeExhausted


def _run(awaitable_or_value: Any) -> Any:
    if asyncio.iscoroutine(awaitable_or_value):
        return asyncio.run(awaitable_or_value)
    return awaitable_or_value


def _fake_runner(argv, *, input_text, timeout=600.0):
    return json.dumps(
        {
            "is_error": False,
            "stop_reason": "end_turn",
            "result": '[{"path":"a.py","line":1,"severity":"high","message":"boom"}]',
            "usage": {
                "input_tokens": 5,
                "output_tokens": 7,
                "cache_read_input_tokens": 3,
            },
        }
    )


def test_anthropic_messages_dispatches_to_provider():
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {"provider": "claude-cli", "custom_handler": ClaudeCliLLM(runner=_fake_runner)}
    ]
    try:
        out = _run(
            litellm.anthropic_messages(
                model="claude-cli/claude-haiku-4-5-20251001",
                max_tokens=64,
                system=[
                    {
                        "type": "text",
                        "text": "SYS",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": "Return findings as JSON."}],
            )
        )
    finally:
        litellm.custom_provider_map = saved

    content = out["content"] if isinstance(out, dict) else out.content
    text = content[0]["text"] if isinstance(content[0], dict) else content[0].text
    assert '"path":"a.py"' in text
    usage = out["usage"] if isinstance(out, dict) else out.usage
    cache_read = (
        usage.get("cache_read_input_tokens")
        if isinstance(usage, dict)
        else getattr(usage, "cache_read_input_tokens", 0)
    )
    assert cache_read == 3


_SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def _structured_runner(argv, *, input_text, timeout=600.0):
    """Records argv, and replies as the CLI does on the structured path."""
    _structured_runner.argv = argv  # type: ignore[attr-defined]
    return json.dumps(
        {
            "is_error": False,
            "stop_reason": "tool_use",
            "result": '{"a":"x"}',
            "structured_output": {"a": "x"},
            "usage": {"input_tokens": 5, "output_tokens": 7},
        }
    )


def test_completion_carries_schema_and_returns_structured_output():
    """Through litellm.completion: schema reaches argv, parsed object survives."""
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_structured_runner),
        }
    ]
    try:
        resp = litellm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "go"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "v", "schema": _SCHEMA},
            },
        )
    finally:
        litellm.custom_provider_map = saved

    argv = _structured_runner.argv
    assert "--json-schema" in argv
    assert argv[argv.index("--json-schema") + 1] == json.dumps(
        _SCHEMA, separators=(",", ":")
    )
    # This is the coupling a downstream consumer pins by name.
    assert resp.structured_output == {"a": "x"}
    assert resp.choices[0].message.content == '{"a":"x"}'
    assert resp.choices[0].finish_reason == "stop"


def test_pydantic_response_format_normalised_by_litellm():
    """A Pydantic response_format reaches the provider as the same json_schema dict."""
    from pydantic import BaseModel

    class Verdict(BaseModel):
        a: str

    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_structured_runner),
        }
    ]
    try:
        litellm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "go"}],
            response_format=Verdict,
        )
    finally:
        litellm.custom_provider_map = saved

    argv = _structured_runner.argv
    assert "--json-schema" in argv
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["a"]["type"] == "string"


def test_anthropic_messages_drops_structured_output():
    """PINNED LIMITATION: the anthropic_messages transform discards the attribute.

    litellm.anthropic_messages() rebuilds the response as a fixed-key Anthropic dict,
    so neither ModelResponse.structured_output nor provider_specific_fields survives.
    A caller needing the parsed object must use litellm.completion(). The JSON string
    is still there in content. If a future litellm makes this pass, that is good news
    — update the assertion and the README/CHANGELOG note together.
    """
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_structured_runner),
        }
    ]
    try:
        out = _run(
            litellm.anthropic_messages(
                model="claude-cli/claude-haiku-4-5-20251001",
                max_tokens=64,
                messages=[{"role": "user", "content": "go"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "v", "schema": _SCHEMA},
                },
            )
        )
    finally:
        litellm.custom_provider_map = saved

    # The schema still reaches the CLI on this path — only the return shape differs.
    assert "--json-schema" in _structured_runner.argv

    structured = (
        out.get("structured_output")
        if isinstance(out, dict)
        else getattr(out, "structured_output", None)
    )
    assert structured is None, "limitation changed — update README and CHANGELOG too"

    content = out["content"] if isinstance(out, dict) else out.content
    text = content[0]["text"] if isinstance(content[0], dict) else content[0].text
    assert text == '{"a":"x"}'


def _exhausted_runner(argv, *, input_text, timeout=600.0):
    return json.dumps(
        {
            "is_error": True,
            "result": "You've hit your usage limit · resets 3pm (America/Los_Angeles)",
        }
    )


def test_completion_wraps_provider_exceptions_in_api_connection_error():
    """PINS a LiteLLM behaviour, not a property this package chose or endorses.

    `litellm.completion()` wraps ANY exception a `CustomLLM.completion()` raises in
    `litellm.exceptions.APIConnectionError` — including `ClaudeExhausted`. The
    original exception is not reachable via `__cause__` (LiteLLM raises `from` nothing
    there); it is reachable only by walking `__context__`, which Python populates
    automatically for an exception raised while handling another.

    This test exists so that a future LiteLLM release which stops wrapping (or starts
    setting `__cause__`) is caught here rather than silently invalidating what
    CHANGELOG.md and README.md say about routing on the original exception class.
    A caller who needs to route on `ClaudeExhausted` / `RuntimeError` / `ValueError`
    must either inspect `exc.__context__` or call `ClaudeCliLLM` directly.
    """
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_exhausted_runner),
        }
    ]
    try:
        with pytest.raises(Exception) as exc_info:
            litellm.completion(
                model="claude-cli/claude-haiku-4-5-20251001",
                messages=[{"role": "user", "content": "go"}],
            )
    finally:
        litellm.custom_provider_map = saved

    raised = exc_info.value
    # Not the original class...
    assert not isinstance(raised, ClaudeExhausted)
    assert type(raised).__name__ != "ClaudeExhausted"
    # ...and not reachable via __cause__.
    assert raised.__cause__ is None

    # ...but IS recoverable by walking __context__.
    cur: BaseException | None = raised
    found = None
    while cur is not None:
        if isinstance(cur, ClaudeExhausted):
            found = cur
            break
        cur = cur.__context__
    assert found is not None, (
        "ClaudeExhausted not recoverable from the wrapped exception's __context__ "
        "chain — LiteLLM's wrapping behaviour changed; update CHANGELOG.md/README.md"
    )


def _plain_runner(argv, *, input_text, timeout=600.0):
    return json.dumps(
        {
            "is_error": False,
            "stop_reason": "end_turn",
            "result": "plain text",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
    )


def test_completion_no_structured_output_stays_absent_through_pre_made_response():
    """Drives an absent-structured_output payload through litellm.completion() —
    the pre-made-ModelResponse copy path (`ClaudeCliLLM._run`'s `pre_made_response`
    branch), which the direct-call unit tests in test_provider.py never exercise.

    Pins two contracts on the path a real consumer actually uses:
      - `structured_output` stays an absent attribute, never present-and-None
        (Finding 2) — a naive `pre_made_response.structured_output = getattr(...)`
        would set it unconditionally and this would fail.
      - `"structured_output"` stays an absent key in `provider_specific_fields`
        (Finding 3) — a naive unconditional assignment in `_build_response` would
        set the key to `None` and this would fail.
    """
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {"provider": "claude-cli", "custom_handler": ClaudeCliLLM(runner=_plain_runner)}
    ]
    try:
        resp = litellm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "go"}],
        )
    finally:
        litellm.custom_provider_map = saved

    assert not hasattr(resp, "structured_output")
    assert "structured_output" not in resp.choices[0].message.provider_specific_fields
    assert resp.choices[0].message.content == "plain text"
