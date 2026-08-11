# litellm-claude-cli

A [LiteLLM](https://github.com/BerriAI/litellm) `CustomLLM` provider backed by the local `claude` CLI subscription.

Exposes a `claude-cli/<model>` namespace so you can call the local Claude subscription through the standard LiteLLM interface — no API key, no network request, just your existing subscription.

## Install

```bash
# via uv
uv add "litellm-claude-cli @ git+https://github.com/cdowell-swtr/litellm-claude-cli@v0.2.0"

# via pip
pip install "litellm-claude-cli @ git+https://github.com/cdowell-swtr/litellm-claude-cli@v0.2.0"
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

## Structured output

Pass a JSON Schema with LiteLLM's standard `response_format` and the provider forwards it
to `claude -p --json-schema`:

```python
import litellm
from litellm_claude_cli import register

register()

resp = litellm.completion(
    model="claude-cli/claude-haiku-4-5-20251001",
    messages=[{"role": "user", "content": "Report the colour and count."}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "sky",
            "schema": {
                "type": "object",
                "properties": {"colour": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["colour", "count"],
            },
        },
    },
)

resp.structured_output           # {"colour": "blue", "count": 3} — the CLI's parsed object
resp.choices[0].message.content  # '{"colour":"blue","count":3}' — the same JSON as a string
```

A Pydantic model as `response_format` works too; LiteLLM normalises it to the shape above.

`structured_output` is set only when the CLI returned a parsed object, so read it as
`getattr(resp, "structured_output", None)` and fall back to `message.content`.

**Use `litellm.completion()` for this.** `litellm.anthropic_messages()` rebuilds the
response as a fixed-key Anthropic dict and drops `structured_output`; the schema is still
applied and the JSON string still arrives in the text content, but the parsed object does not
survive that path.

**Schema size:** the CLI takes the schema inline with no file-path option, so a compact
encoding over 131072 bytes (Linux `MAX_ARG_STRLEN`) raises `ValueError` before the call runs.

## How it works

Each call shells out to `claude -p` with all agentic tools disabled so every call is exactly one model turn. The system prompt is written to a temp file (never passed as an argv element) to avoid Linux's `MAX_ARG_STRLEN` limit (~128 KB). Cache token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`) are propagated through to the LiteLLM `Usage` object.

## Public API

- `ClaudeCliLLM` — the `CustomLLM` subclass; accepts an optional `runner` argument for testing
- `ClaudeExhausted` — raised when `claude -p` signals subscription exhaustion; carries an optional `reset_hint`
- `register()` — idempotently registers `ClaudeCliLLM` under the `claude-cli` provider
