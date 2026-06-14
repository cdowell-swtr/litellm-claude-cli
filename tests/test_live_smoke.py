"""Real `claude` CLI end-to-end through litellm. Opt-in: set RUN_LIVE_SMOKE=1 with the
`claude` CLI on PATH (subscription). The package owns the claude -p mechanics, so it
verifies them independently of the framework."""

from __future__ import annotations

import asyncio
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
