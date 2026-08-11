"""Proves the provider actually plugs into litellm: anthropic_messages(model="claude-cli/...")
must dispatch to ClaudeCliLLM and round-trip a well-formed response. Fully offline
(fake subprocess runner — no network, no key, no real `claude`)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import litellm
from litellm_claude_cli import ClaudeCliLLM


def _run(awaitable_or_value: Any) -> Any:
    if asyncio.iscoroutine(awaitable_or_value):
        return asyncio.run(awaitable_or_value)
    return awaitable_or_value


def _fake_runner(argv, *, input_text):
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


def _structured_runner(argv, *, input_text):
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
