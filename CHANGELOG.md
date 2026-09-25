# Changelog

## 0.4.0

### Exclusive mode — an allowlist that bounds the tool set

`Capabilities` gains `exclusive: bool = False`. With `exclusive=True`, `tools` is the
call's whole tool set:

```python
Capabilities(tools=("WebSearch", "WebFetch"), exclusive=True)  # exactly these two
Capabilities(exclusive=True)                                   # no tools at all
```

argv carries `--tools <tools joined by ",">` in the caller's order, plus
`--strict-mcp-config`, and no `--disallowed-tools`. `tools=()` becomes `--tools ""`.

The deny list never bounded the tool set. `_DISABLED_TOOLS` names ten tools, and the CLI
ships others it does not name (`Monitor`, `CronCreate`, `TaskCreate`, `SendMessage`,
`Workflow`, …), with more in every release. `Monitor` runs shell commands, and it has
been observed doing so under `capabilities=None`. The docs no longer claim the deny list
makes a call one model turn.

- **Names:** the valid names are the same ten, matched exactly. The CLI silently drops
  an unknown or wrong-case `--tools` name (`--tools webfetch` yields no tools), so the
  provider's `ValueError` is the only place a typo surfaces.
- **Browser:** `browser=True` with `exclusive=True` raises `ValueError`. The browser's
  tools arrive as an MCP server, and whether `--strict-mcp-config` admits them is
  unmeasured.
- **Default:** `exclusive=False` leaves argv byte-identical to 0.3.2.

### Usage counts every model

`Usage` is now summed across every entry in the payload's `modelUsage`. Top-level `usage`
is used only when `modelUsage` is absent or empty.

- **Why:** top-level `usage` covers the call's own model alone. WebSearch and WebFetch
  run on a second model (haiku) that only `modelUsage` reports. In a measured sonnet
  research call, that second model's input exceeded the entire recorded spend, and none
  of it reached the caller.
- **The sum:** `prompt_tokens` = Σ `inputTokens`, `completion_tokens` = Σ
  `outputTokens`, and the two cache fields likewise. Where the two sources disagree for
  the call's own model, the per-model figure wins, because it is the breakdown the
  top-level figure summarises.
- **Mixed models:** the sum mixes models, so it cannot be priced at any one model's
  rate.
- **Per-model figures:** the raw `modelUsage` dict is attached verbatim as
  `provider_specific_fields["model_usage"]`, for callers that attribute spend per model.
  It is absent when the payload carries none.
- **Thinking tokens are not added.** On three haiku calls, `outputTokens` minus
  `thinkingTokens` was 15–16, the length of the one-word reply, so `outputTokens`
  already includes thinking.

For a tool-using call, `Usage` now reports more than 0.3.2 did for the same call. For a
call that uses no second model, it is unchanged.


### Verified against

`claude` CLI 2.1.282. A live test reads the CLI's `system/init` event for the provider's
own argv. For `("WebSearch", "WebFetch")` and for `()`, the effective tool set equals the
grant and `mcp_servers` is `[]`. A live sonnet call granted `WebSearch` reports two
models in `modelUsage`, and the recorded `prompt_tokens` equals their summed
`inputTokens`.

## 0.3.2

### Skills disabled on every call

Every `claude -p` invocation now carries `--disable-slash-commands`. It is fixed argv,
alongside `--exclude-dynamic-system-prompt-sections` — not a capability.

Two things rest on it. A one-shot `-p` call resolves no slash command, so the skill
listing the CLI injects into each call's context is dead weight. And the `Skill` tool
sits outside `_DISABLED_TOOLS` — it was never disabled — so a skill was the one
remaining route by which a call could take a second turn; this flag closes it and the
one-model-turn invariant now holds against the CLI's full tool surface.

Callers pass nothing and lose nothing on this path. There is no capability to grant
skills back: one would reopen the second-turn route the flag exists to close.

**argv is no longer byte-identical to the pre-0.3.0 build** for a caller passing no
`capabilities`. 0.3.0's byte-identity claim held only against builds that predate this
flag; the whole-argv literal in `tests/test_capabilities.py` is the current pin.

### Context cost — the sign flips with CLI version

This flag was requested as a context-cost saving, and whether it is one depends on which
`claude` CLI you run. Interleaved A/B (identical argv but for the flag, warm, byte-stable
per arm, run on both CLIs on one machine):

| CLI | without flag | with flag | effect |
|---|---|---|---|
| 2.1.235 | 19,673 | 17,746 | **saves** 1,927 tokens/call |
| 2.1.259 | 24,038 | 26,233 | **costs** 2,195 tokens/call |

Measured independently on both sides and reproduced across them. Whatever 2.1.259 adds to
the prefix when skills are off is real and version-specific; the mechanism is unidentified.
The flag ships on the one-model-turn argument above, which holds either way. If you are
adopting it for cost, A/B it on your own CLI version and re-run that A/B on every CLI
upgrade — the sign is not stable across versions.

### Verified against

`claude` CLI 2.1.259, litellm 1.89.0. The declared floor remains `litellm>=1.88.1`.
Live smoke suite (`RUN_LIVE_SMOKE=1`) passes against the real CLI with the flag in argv.

## 0.3.1

### Configurable call timeout

`ClaudeCliLLM` accepts an optional `timeout` parameter (seconds), the wall-clock
ceiling for one `claude -p` subprocess:

```python
llm = ClaudeCliLLM(timeout=5100)
```

Defaults to `DEFAULT_TIMEOUT` (600.0) — the value hardcoded through v0.3.0, so
omitting it changes nothing. It was sized for judgement-shaped calls; an agentic
caller whose own scheduling grants a call more time (a lease, a queue TTL) passes
that budget here so the subprocess kill and the scheduler agree. Invalid values
(zero, negative, non-numeric, bool) raise `ValueError` at construction.

The `_Runner` protocol gains the keyword: `(argv, *, input_text, timeout) -> str`.
Custom runners must accept it; `_default_runner` keeps its own 600.0 default.

## 0.3.0

### First-class capabilities

`ClaudeCliLLM` accepts an optional `Capabilities(tools=..., browser=...)` parameter,
a pinned public type:

```python
from litellm_claude_cli import Capabilities, ClaudeCliLLM

llm = ClaudeCliLLM(capabilities=Capabilities(tools=("Read", "Grep"), browser=True))
```

`tools` names the tools to grant; each is subtracted from the disable list, so the
valid names are exactly the disabled-by-default set. Matching is exact and
case-sensitive — an unknown or wrong-case name raises `ValueError` rather than
silently granting nothing. `browser=True` appends `--chrome` and is supported with
no granted tools at all.

Omitting `capabilities` (or passing `None`) disables all ten listed tools, unchanged from
prior releases — argv is byte-identical to the pre-`Capabilities` build in that
case, pinned by test.

### `finish_reason` — behaviour change

`tool_use` now maps to `finish_reason: "stop"` **unconditionally**, not only when a
JSON schema was requested (0.2.0's behaviour). This provider never populates a
`tool_calls` array on the response, so LiteLLM's `tool_use` → `"tool_calls"`
mapping would hand a caller something no array backs, regardless of which granted
tool produced the CLI's `tool_use`. The CLI's raw value is unchanged and still
surfaced in `provider_specific_fields["stop_reason"]`.

This does not change behaviour for a caller passing no `capabilities`: with every
tool disabled, a requested JSON schema is the only thing that can produce
`tool_use`, and that case already mapped to `"stop"` in 0.2.0.

### Verified against

`claude` CLI 2.1.227, litellm 1.89.0. The declared floor remains `litellm>=1.88.1`.

## 0.2.0

### Structured output

Callers can now constrain output to a JSON Schema using LiteLLM's standard
`response_format`, which the provider passes to `claude -p` as `--json-schema`.
A Pydantic model works too — LiteLLM normalises it to the same shape before the
provider sees it.

```python
resp = litellm.completion(
    model="claude-cli/claude-haiku-4-5-20251001",
    messages=[...],
    response_format={"type": "json_schema", "json_schema": {"name": "v", "schema": {...}}},
)

resp.structured_output              # the CLI's parsed object
resp.choices[0].message.content     # the same JSON, as a string
```

**`structured_output` is the pinned public name.** It is set only when the CLI
returned a parsed object — absent otherwise, never present-and-`None`. Read it as
`getattr(resp, "structured_output", None)` and fall back to
`resp.choices[0].message.content`, which always carries the JSON string.

`resp.choices[0].message.provider_specific_fields` carries the CLI's raw `stop_reason`,
which is surfaced on every call, plus the parsed object under the `structured_output`
key — present only when the CLI returned one.

### `finish_reason` on the structured path — behaviour change

The CLI implements structured output as a forced tool call and reports
`stop_reason: "tool_use"`, which LiteLLM maps to `finish_reason: "tool_calls"`. That
tells a caller to execute tool calls and continue the loop, but no `tool_calls` array
is exposed and there is nothing to execute — a tool-runner loop would error or spin.

When a schema was sent and the CLI reported `tool_use`, `finish_reason` is now `"stop"`.
No other stop reason is rewritten. The CLI's raw value stays available in
`provider_specific_fields["stop_reason"]`.

This mapping is sound only because every agentic tool is disabled on every call, making
`tool_use` unambiguous within this provider.

### Known limitation — use `completion()` for the parsed object

`litellm.anthropic_messages()` rebuilds the response as a fixed-key Anthropic dict and
discards both `structured_output` and `provider_specific_fields`. The schema is still
passed to the CLI on that path and the JSON string still arrives in the text content,
but **a caller needing the parsed object must use `litellm.completion()`**.

### Schema size ceiling

The CLI accepts a schema only as an inline argument, with no file-path option, so it is
subject to Linux's `MAX_ARG_STRLEN`. A schema whose compact encoding reaches 131072
bytes or more raises `ValueError` before any subprocess runs, rather than failing
opaquely at exec. (131072 is the first *rejected* length, not the last accepted one —
Linux's own check counts the NUL terminator.)

The provider itself raises three mutually distinguishable exceptions —
`ClaudeExhausted` (subscription exhaustion), `RuntimeError` (malformed CLI output),
and this `ValueError` (oversized schema) — and that distinction is real when you call
`ClaudeCliLLM` directly. It does **not** carry through `litellm.completion()`:
LiteLLM wraps *any* exception a `CustomLLM` raises in
`litellm.exceptions.APIConnectionError`, so all three arrive at that entry point as
the same wrapper class. To route on the original exception, either inspect the
wrapped exception's `__context__` chain (LiteLLM does not set `__cause__`) or call
`ClaudeCliLLM` directly instead of going through `litellm.completion()`.

### Verified against

`claude` CLI 2.1.227, litellm 1.89.0. The declared floor remains `litellm>=1.88.1`.

## 0.1.1

- Ship `py.typed` marker (PEP 561).

## 0.1.0

- Initial release: `claude-cli/<model>` provider wrapping headless `claude -p`.
