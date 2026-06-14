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
