# Structured output through the claude-cli provider — design

*Design spec. Target version `0.2.0`. Task: LCC3.*

## 1. Purpose

`litellm-claude-cli` returns text only. This adds schema-constrained JSON: the caller supplies a
JSON Schema through LiteLLM's standard `response_format`, the provider passes it to `claude -p`
via `--json-schema`, and the CLI's own parse of the constrained output is surfaced on the
response.

Two changes, separable:

1. **Schema in** — accept a JSON Schema and pass it to the CLI.
2. **Parsed object out** — surface the CLI's `structured_output` on the `ModelResponse`.

Change 1 alone is sufficient for a caller to get parseable JSON, because the CLI's `result`
field already carries the same JSON as a string. Change 2 earns its place by removing a second,
independently-fallible parse on the caller's side. Both ship in `0.2.0`.

## 2. Verified environment

Every fact below was confirmed by execution, not read from documentation. Re-verify before
relying on any of it against a different CLI or LiteLLM version.

| Fact | Verified against |
|---|---|
| `--json-schema <schema>` exists; takes the schema **inline as a JSON string**, not a file path | `claude` CLI 2.1.227 |
| On success, `--output-format json` payload gains top-level `structured_output` (parsed object) alongside `result` (same JSON, as a string) | `claude` CLI 2.1.227 |
| `stop_reason` returns `"tool_use"` — structured output is implemented as a forced tool call | `claude` CLI 2.1.227 |
| `response_format` reaches a `CustomLLM` untransformed at `kwargs["optional_params"]["response_format"]` | litellm 1.89.0 |
| A Pydantic model passed as `response_format` is normalised by LiteLLM into the same `{"type":"json_schema","json_schema":{...}}` dict before it reaches the provider | litellm 1.89.0 |
| An arbitrary attribute set on the returned `ModelResponse` survives `litellm.completion()` | litellm 1.89.0 |
| `litellm.anthropic_messages()` transforms the response into a fixed-key dict; both the custom attribute and `provider_specific_fields` are **dropped** | litellm 1.89.0 |
| `ModelResponse` construction maps `finish_reason="tool_use"` to OpenAI's `"tool_calls"` | litellm 1.89.0 |

The LiteLLM facts were verified on 1.89.0; the package's declared floor is `litellm>=1.88.1`, which
was not separately exercised. The floor is left unchanged — nothing here relies on an API introduced
after it — but the gap is untested, so a consumer pinned below 1.89.0 is on unverified ground.

## 3. Input surface

The caller uses LiteLLM's standard `response_format` on `litellm.completion()`. No provider-specific
kwarg is introduced.

```python
resp = litellm.completion(
    model="claude-cli/claude-haiku-4-5-20251001",
    messages=[...],
    response_format={"type": "json_schema", "json_schema": {"name": "verdicts", "schema": {...}}},
)
```

A Pydantic model is equally valid — LiteLLM normalises it to the dict form above before the
provider sees it, so the provider handles exactly one shape.

**Extraction.** The provider reads `optional_params["response_format"]`. When it is a mapping with
`type == "json_schema"`, the schema is `response_format["json_schema"]["schema"]`.

**Ignored inputs, deliberately.** `{"type": "json_object"}` carries no schema, so there is nothing
to pass; it is ignored rather than raising. Every other key in `optional_params` continues to be
ignored, as before this change.

## 4. argv construction

When a schema is present, append to argv:

```
--json-schema <compact JSON>
```

encoded with `json.dumps(schema, separators=(",", ":"))`. Nothing else about argv changes.

**Invariants that must survive** — each is load-bearing and none is visible from reading the code:

- `--system-prompt-file` and `--exclude-dynamic-system-prompt-sections` stay. Omitting them pulls
  ~27k cache-creation and ~25k cache-read tokens of Claude Code's own harness prompt into a call
  with 19 tokens of real input.
- `--bare` is never added. Its help text describes exactly what a batch caller wants — skips hooks,
  LSP, plugin sync, auto-memory, CLAUDE.md discovery — but it forces auth to `ANTHROPIC_API_KEY` /
  `apiKeyHelper` and never reads OAuth or the keychain. Every call would move silently onto metered
  API billing, defeating the purpose of this package.
- The `_DISABLED_TOOLS` list stays. One model turn per call is the contract, and §6 depends on it.

### 4.1 The argv size ceiling

The schema goes in argv and the CLI offers no file-path alternative, which reintroduces the
`MAX_ARG_STRLEN` (131072 bytes) ceiling this package already dodges for the system prompt. Exceeding
it surfaces as an opaque exec failure.

Before invoking the runner, if the encoded schema's UTF-8 length reaches or exceeds 131072 bytes,
raise `ValueError` naming both the actual size and the ceiling. The comparison is `>=`, not `>`:
Linux's own `MAX_ARG_STRLEN` check counts the NUL terminator, so a length of exactly 131072 bytes
is already the first rejected length, not the last accepted one. Guarded, not merely documented:
the failure it replaces is unreadable, and `ValueError` keeps the error taxonomy in §7 clean.

## 5. Output surface

On a call that carried a schema:

```python
resp.structured_output                            # parsed object
resp.choices[0].message.content                   # the same JSON, as a string
resp.choices[0].message.provider_specific_fields  # {"structured_output": {...}, "stop_reason": "tool_use"}
```

**`structured_output` is the pinned public name.** Consumers couple to it by name. It is set **only
when the CLI payload contains a `structured_output` key** — absent otherwise, never present-and-`None`.
A consumer reads it as `getattr(resp, "structured_output", None)` and falls back to
`resp.choices[0].message.content`.

**`message.content` always carries the JSON string**, schema or no schema. This is what callers
parsing today already receive, and it keeps the fallback above honest.

**A missing `structured_output` is not an error at this layer.** `result` still carries the JSON, and
a consumer that routes "structured output absent" as its own failure mode can only do so if the
provider does not pre-empt it.

**`provider_specific_fields`** carries a copy of the parsed object and the CLI's raw `stop_reason`.
The raw `stop_reason` is surfaced **unconditionally**, not only on the structured path — it is a
pass-through of what the CLI reported, and making it conditional would mean the case most worth
inspecting is the one that looks different.

## 6. The `finish_reason` mapping

When a schema was sent **and** the CLI reported `stop_reason: "tool_use"`, `finish_reason` is `"stop"`.
In every other case it is unchanged. Blanket rewriting of `tool_use` wherever it appears is explicitly
out of scope.

**Why.** `finish_reason` is not a provider-reported field being overridden; it is a normalised field
whose meaning is fixed by the interface. `tool_calls` means "the caller is expected to execute
something and continue the loop". Here there is nothing to execute — the tool call is an
implementation detail of how the CLI produces structured output, and no `tool_calls` array is
exposed. Left alone, LiteLLM maps the CLI's `tool_use` to `tool_calls`, and a tool-runner loop
branching on that field either errors on the missing array or spins. Translating into the interface's
vocabulary is what an adapter is for; leaking the backend's is the failure mode.

**Soundness condition — record this next to the mapping in code.** The mapping is sound only because
`_DISABLED_TOOLS` disables every tool, so within this provider `tool_use` has exactly one possible
cause: the forced tool call implementing structured output. **If the tool-disable list is ever
relaxed, this mapping stops being sound.** The same constraint that makes each call exactly one model
turn is what makes this translation unambiguous.

Nothing is destroyed: the CLI's raw `stop_reason` remains one field away in
`provider_specific_fields` (§5). That is what makes this translation rather than erasure.

## 7. Error taxonomy

Three failure kinds stay mutually distinguishable, because consumers route them differently —
exhaustion pauses and retries without penalty, malformed output is a task failure, auth failure exits.

| Condition | Raised |
|---|---|
| Subscription exhaustion | `ClaudeExhausted` (carries `reset_hint`) — unchanged |
| Non-JSON / malformed CLI output, or a CLI error that is not exhaustion | `RuntimeError` — unchanged |
| Encoded schema reaches or exceeds `MAX_ARG_STRLEN` | `ValueError` — new |

`ValueError` is the right class for the new case: it is a caller error about a caller-supplied
argument, detected before any subprocess runs, and it collides with neither existing kind.

## 8. Known limitation — `anthropic_messages()`

`litellm.anthropic_messages()` transforms the `ModelResponse` into a fixed-key Anthropic-shaped dict.
Both `resp.structured_output` and `message.provider_specific_fields` are dropped; the caller receives
only `content[0].text`, which still holds the JSON string.

**Change 1 works on both entry points. Change 2 is available on `litellm.completion()` only.** A
caller needing the parsed object must use `completion()`. This is pinned by a test (§9) so it stays a
documented property of the seam rather than something rediscovered later, and it is stated in the
README and the release notes.

## 9. Testing

**Unit, against the existing injected `runner`:**

- `--json-schema` reaches argv, immediately followed by the compact encoding of the inner schema.
- No `--json-schema` in argv when `response_format` is absent, and none when it is `{"type": "json_object"}`.
- `structured_output` is set on the response when the payload carries it; absent (`hasattr` false)
  when it does not, while `message.content` still holds the string.
- `provider_specific_fields` carries the parsed object and the raw `stop_reason`.
- `finish_reason` is `stop` for schema + `tool_use`; unchanged for `tool_use` without a schema, and
  unchanged for any other `stop_reason`.
- A schema encoding to 131072 bytes or more raises `ValueError` naming the size and the ceiling —
  this is the test pinning the argv-size decision, including the exact-boundary case.
- The `--system-prompt-file`, `--exclude-dynamic-system-prompt-sections` and `--disallowed-tools`
  invariants still hold on the structured path; `--bare` is absent.

**Dispatch, offline:**

- Through `litellm.completion()` with a fake runner: `response_format` reaches the provider and
  `resp.structured_output` survives the round trip.
- Through `litellm.anthropic_messages()`: pins §8 — the attribute does not survive, and
  `content[0].text` still carries the JSON string.

**Live, behind `RUN_LIVE_SMOKE=1`** (matching existing smoke-test gating): a real `claude` call with a
small schema, asserting the returned object conforms. Unit tests cannot catch a wrong flag name; only
this can.

## 10. Release

- Version `0.2.0` — additive, but the `finish_reason` mapping changes observable behaviour for any
  caller already passing a schema-shaped `response_format`.
- Tag `v0.2.0`. The known consumer installs by git tag, so an untagged commit is unconsumable.
- New `CHANGELOG.md` carrying release notes that name `structured_output` explicitly as the pinned
  public surface, and state both the `anthropic_messages` limitation (§8) and the argv ceiling (§4.1).
- README gains a structured-output usage block; install pins move to `v0.2.0`.
