"""Real `claude` CLI end-to-end through litellm. Opt-in: set RUN_LIVE_SMOKE=1 with the
`claude` CLI on PATH (subscription). The package owns the claude -p mechanics, so it
verifies them independently of the framework."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import litellm
import pytest
from litellm_claude_cli import ClaudeCliLLM


def _run(v: Any) -> Any:
    return asyncio.run(v) if asyncio.iscoroutine(v) else v


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1" or shutil.which("claude") is None,
    reason="live: set RUN_LIVE_SMOKE=1 with the `claude` CLI on PATH",
)
def test_live_claude_cli_dispatch():
    big = "x = 1\n" + (
        "# pad\n" * 40000
    )  # > MAX_ARG_STRLEN; must go via temp file + stdin
    assert len(big) > 131072
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {"provider": "claude-cli", "custom_handler": ClaudeCliLLM()}
    ]
    try:
        out = _run(
            litellm.anthropic_messages(
                model="claude-cli/claude-haiku-4-5-20251001",
                max_tokens=64,
                system=[{"type": "text", "text": f"Reply with []. Context:\n{big}"}],
                messages=[{"role": "user", "content": "Return [] as a JSON array."}],
            )
        )
    finally:
        litellm.custom_provider_map = saved
    content = out["content"] if isinstance(out, dict) else out.content
    assert content


@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1" or shutil.which("claude") is None,
    reason="live: set RUN_LIVE_SMOKE=1 with the `claude` CLI on PATH",
)
def test_live_structured_output_conforms():
    """Real `claude --json-schema` call: output is constrained and parsed for us."""
    schema = {
        "type": "object",
        "properties": {
            "colour": {"type": "string", "enum": ["red", "green", "blue"]},
            "count": {"type": "integer"},
        },
        "required": ["colour", "count"],
        "additionalProperties": False,
    }
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {"provider": "claude-cli", "custom_handler": ClaudeCliLLM()}
    ]
    try:
        resp = litellm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[
                {
                    "role": "user",
                    "content": "The sky is blue and there are 3 clouds. Report the colour and count.",
                }
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "sky", "schema": schema},
            },
        )
    finally:
        litellm.custom_provider_map = saved

    obj = getattr(resp, "structured_output", None)
    assert obj is not None, (
        "CLI returned no structured_output — check the --json-schema flag name"
    )
    assert obj["colour"] in {"red", "green", "blue"}
    assert isinstance(obj["count"], int)
    # content carries the same JSON as a string.
    assert json.loads(resp.choices[0].message.content) == obj
    # The forced tool call must not leak as tool_calls.
    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.provider_specific_fields["stop_reason"] == "tool_use"
