# litellm-claude-cli

A [LiteLLM](https://github.com/BerriAI/litellm) `CustomLLM` provider backed by the local `claude` CLI subscription.

Exposes a `claude-cli/<model>` namespace so you can call the local Claude subscription through the standard LiteLLM interface — no API key, no network request, just your existing subscription.

## Install

```bash
# via uv
uv add "litellm-claude-cli @ git+https://github.com/cdowell-swtr/litellm-claude-cli@v0.1.1"

# via pip
pip install "litellm-claude-cli @ git+https://github.com/cdowell-swtr/litellm-claude-cli@v0.1.1"
```

**Requires:** Python 3.12+, `litellm>=1.88.1`, and the `claude` CLI installed and authenticated on PATH.

## Usage

LiteLLM 1.88.1 has no entry-point auto-registration for `CustomLLM` providers. Call `register()` once at startup before making any `claude-cli/` calls:

```python
from litellm_claude_cli import register

register()  # adds the `claude-cli` provider to litellm.custom_provider_map
```

Then use `litellm.anthropic_messages` (or `litellm.completion`) with the `claude-cli/<model>` prefix:

```python
import litellm
from litellm_claude_cli import register

register()

response = litellm.anthropic_messages(
    model="claude-cli/claude-haiku-4-5-20251001",
    max_tokens=1024,
    system=[{"type": "text", "text": "You are a helpful assistant."}],
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.content[0].text)
```

`register()` is idempotent — safe to call multiple times; existing `claude-cli` entries are replaced rather than duplicated.

## How it works

Each call shells out to `claude -p` with all agentic tools disabled so every call is exactly one model turn. The system prompt is written to a temp file (never passed as an argv element) to avoid Linux's `MAX_ARG_STRLEN` limit (~128 KB). Cache token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`) are propagated through to the LiteLLM `Usage` object.

## Public API

- `ClaudeCliLLM` — the `CustomLLM` subclass; accepts an optional `runner` argument for testing
- `ClaudeExhausted` — raised when `claude -p` signals subscription exhaustion; carries an optional `reset_hint`
- `register()` — idempotently registers `ClaudeCliLLM` under the `claude-cli` provider
