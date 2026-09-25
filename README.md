# litellm-claude-cli

A [LiteLLM](https://github.com/BerriAI/litellm) `CustomLLM` provider backed by the local `claude` CLI subscription.

Exposes a `claude-cli/<model>` namespace so you can call the local Claude subscription through the standard LiteLLM interface — no API key, no network request, just your existing subscription.

## Install

```bash
# via uv
uv add "litellm-claude-cli @ git+https://github.com/cdowell-swtr/litellm-claude-cli@v0.3.0"

# via pip
pip install "litellm-claude-cli @ git+https://github.com/cdowell-swtr/litellm-claude-cli@v0.3.0"
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
encoding that reaches 131072 bytes or more (Linux `MAX_ARG_STRLEN`) raises `ValueError`
before the call runs. (131072 is the first *rejected* length, not the last accepted one —
Linux's own check counts the NUL terminator.)

**Error routing:** `ClaudeCliLLM` raises three mutually distinguishable exceptions —
`ClaudeExhausted` (subscription exhaustion), `RuntimeError` (malformed CLI output), and
`ValueError` (oversized schema) — and calling `ClaudeCliLLM` directly preserves that
distinction. Calling through `litellm.completion()` does not: LiteLLM wraps *any*
exception a `CustomLLM` raises in `litellm.exceptions.APIConnectionError`, so all three
arrive there as the same wrapper class. To route on the original exception, either
inspect the wrapped exception's `__context__` chain (LiteLLM does not set `__cause__`)
or call `ClaudeCliLLM` directly.

## Capabilities

By default, the ten tools in `_DISABLED_TOOLS` (`Bash`, `Read`, `Edit`, `Write`,
`Grep`, `Glob`, `WebFetch`, `WebSearch`, `Task`, `NotebookEdit`) are disabled. Pass an
optional `Capabilities` to `ClaudeCliLLM` to grant specific tools and/or attach the
browser:

```python
from litellm_claude_cli import Capabilities, ClaudeCliLLM

llm = ClaudeCliLLM(capabilities=Capabilities(tools=("Read", "Grep"), browser=True))
```

The other listed tools stay disabled — `tools` only grants the ones you name.
Names are validated against the disable list and must match exactly
(case-sensitive); an unknown or wrong-case name raises `ValueError`.
`browser=True` appends `--chrome` and is supported with no granted tools at
all.

Omitting `capabilities` disables all ten listed tools, as before.

**The deny list is not a boundary.** The CLI ships tools `_DISABLED_TOOLS` does not
name (`Monitor`, `CronCreate`, `TaskCreate`, `SendMessage`, `Workflow`, …), and every
release adds more. `Monitor` runs shell commands: a model asked to use it will, under
`capabilities=None`. To bound what a call can touch, use exclusive mode.

### Exclusive mode — an allowlist

```python
Capabilities(tools=("WebSearch", "WebFetch"), exclusive=True)  # exactly these two
Capabilities(exclusive=True)                                   # no tools at all
```

With `exclusive=True`, `tools` is the call's **whole** tool set: argv carries
`--tools WebSearch,WebFetch` (the caller's order) and `--strict-mcp-config`, and no
`--disallowed-tools`. `tools=()` becomes `--tools ""`, which the CLI reads as no tools.
The valid names are the same ten, validated the same way — this matters more here,
because the CLI silently drops an unknown or wrong-case name from `--tools`, leaving
the tool absent while the caller believes it granted. `browser=True` with
`exclusive=True` raises `ValueError`: the browser's tools arrive as an MCP server, and
whether `--strict-mcp-config` admits them is unmeasured. The default,
`exclusive=False`, leaves argv exactly as before.

**`register()` takes no capabilities.** `register()` always installs a plain
`ClaudeCliLLM()` with `capabilities=None` — a process-global capability grant
would contradict the deliberately per-instance design. A caller that needs
`Capabilities` registers a configured handler directly instead:

```python
litellm.custom_provider_map = [
    {"provider": "claude-cli", "custom_handler": ClaudeCliLLM(capabilities=Capabilities(tools=("Read",)))}
]
```

**Granting a tool is not the same as permitting it.** Outside exclusive mode,
`tools` only removes that name's `--disallowed-tools` flag; in either mode argv carries no `--permission-mode` and
no `--allowed-tools`, so the CLI's own permission layer still gates tool use
in headless `-p`. Only `Read` has been proven to work end-to-end this way —
`Bash`, `Edit`, and `Write` may still be refused. A refusal surfaces as
`finish_reason: "stop"` with no `structured_output` and a prose refusal in the
content, indistinguishable from ordinary invalid model output except by the
raw `stop_reason` in `provider_specific_fields`. Relatedly, a granted file
tool only reaches paths under the CLI's working directory.

**`finish_reason` note:** a granted tool can make the CLI report `stop_reason:
"tool_use"`, same as the structured-output path above. This provider never
populates a `tool_calls` array, so `tool_use` always maps to `finish_reason:
"stop"` — the raw value is still available in
`provider_specific_fields["stop_reason"]`. As with structured output above,
`litellm.anthropic_messages()` drops `provider_specific_fields` entirely, so a
caller on that path has no way to detect a truncated `tool_use` turn; use
`litellm.completion()` if you need to.

## How it works

Each call shells out to `claude -p` with `--disable-slash-commands`, plus either an allowlist (`--tools … --strict-mcp-config`, in exclusive mode) or the ten tools in `_DISABLED_TOOLS` denied (unless `Capabilities` grants some back). Only the allowlist bounds the tool set; see [Capabilities](#capabilities). Skills are disabled unconditionally: a one-shot `-p` call resolves no slash command, and the `Skill` tool sits outside `_DISABLED_TOOLS`. There is no capability to grant skills back. The system prompt is written to a temp file (never passed as an argv element) to avoid Linux's `MAX_ARG_STRLEN` limit (~128 KB). `Usage` sums every model the call drove: the CLI's per-model `modelUsage` (a web tool runs on a second model that top-level `usage` omits), falling back to top-level `usage` when absent. Cache token fields (`cache_read_input_tokens`, `cache_creation_input_tokens`) are carried the same way. The raw per-model breakdown is in `provider_specific_fields["model_usage"]`.

## Public API

- `ClaudeCliLLM` — the `CustomLLM` subclass; accepts optional `runner` (for testing) and `capabilities` arguments
- `Capabilities` — `tools`/`browser` grants for a `ClaudeCliLLM` instance; see [Capabilities](#capabilities) above
- `ClaudeExhausted` — raised when `claude -p` signals subscription exhaustion; carries an optional `reset_hint`
- `register()` — idempotently registers `ClaudeCliLLM` under the `claude-cli` provider
