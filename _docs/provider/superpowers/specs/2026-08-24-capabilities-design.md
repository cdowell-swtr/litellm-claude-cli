# First-class capabilities through the claude-cli provider — design

*Design spec. Target version `0.3.0`. Task: LCC4.*

## 1. Purpose

`ClaudeCliLLM` builds every `claude -p` call with all ten tools disabled, so a call returns exactly
one model turn. Some callers need a narrower guarantee: a specific tool enabled, or the browser
attached, while everything else stays disabled.

This adds `Capabilities` — a frozen, validated description of what a call may touch — as an
optional constructor parameter. The provider builds argv from it. Callers that pass nothing are
unaffected, byte for byte.

The known consumer (`jsp`) today achieves this by wrapping the provider's private `self._runner`
and rewriting the argv the provider just built. That rewrite reaches across a dependency boundary
into internals and survives only because the pin is frozen. Owning the capability here retires it.

## 2. Public surface

```python
@dataclass(frozen=True)
class Capabilities:
    tools: tuple[str, ...] = ()   # tools to ALLOW — subtracted from the disable list
    browser: bool = False         # attach the browser via --chrome

ClaudeCliLLM(runner=..., capabilities: Capabilities | None = None)
```

`Capabilities` is defined in `litellm_claude_cli/__init__.py` and so is importable as
`from litellm_claude_cli import Capabilities`, alongside `ClaudeCliLLM` and `ClaudeExhausted`.
The module declares no `__all__`; none is introduced.

`capabilities` follows `runner` in the signature. Both are optional; consumers pass by keyword.

**Nominal, not structural.** A consumer holding its own richer `Capabilities` type constructs
*this* one at the boundary. The provider never inspects unknown attributes and never duck-types.
Fields that do not reach argv do not belong here.

## 3. `tools` — subtraction, and what makes a name valid

`tools` names tools to **remove from the disable list**. It is not an allowlist: it cannot enable
something the provider never disabled, and it does not describe the full set of tools a call may
use.

That makes the set of valid names exactly `_DISABLED_TOOLS`:

```
Bash · Read · Edit · Write · Grep · Glob · WebFetch · WebSearch · Task · NotebookEdit
```

A name outside that list is either already-enabled — where granting it is meaningless — or a typo.
Both are worth failing on, so `Capabilities.__post_init__` raises `ValueError` naming the offending
value(s) and the valid set. Validation lives on the dataclass, not on `ClaudeCliLLM`, so it fires at
the construction site that holds the mistake, whether or not the object is ever handed to a provider.

Matching is exact and case-sensitive: `"bash"` raises. Silently accepting it would leave the caller
believing a tool is enabled while its disable flag remains — the failure this validation exists to
prevent.

Duplicates are ignored; `tools` has set semantics. With unknown names raising, a duplicate is a
valid name twice, which is harmless.

**When the CLI gains a tool.** `_DISABLED_TOOLS` grows to preserve the one-model-turn invariant, and
subtraction keeps its meaning unchanged. The consumer's hardcoded expectation of the ten is the
tripwire that forces a person to re-read this on any such change.

## 4. argv construction

A module-level helper resolves the disable list:

```python
def _disabled_tools_for(capabilities: Capabilities | None) -> tuple[str, ...]:
    if capabilities is None:
        return _DISABLED_TOOLS
    granted = frozenset(capabilities.tools)
    return tuple(t for t in _DISABLED_TOOLS if t not in granted)
```

Two properties follow structurally rather than by care:

- **`None` is identity.** The `None` path returns `_DISABLED_TOOLS` itself, so the default argv is
  byte-identical to `0.2.0` because it is the same code, not because it was checked.
- **Provider-list order.** Emission order is `_DISABLED_TOOLS` order. The caller's tuple order is
  not observable in argv, so two `Capabilities` granting the same tools always produce identical
  argv.

The capability block is emitted as a unit, after the model/schema flags:

```
[--chrome]                      # iff capabilities.browser
--disallowed-tools <name>       # pairwise, for each remaining name
...
```

`--chrome` sits immediately before the disable pairs so that everything governing what a call may
touch reads as one block. Its position is not otherwise significant, and tests assert presence and
count rather than index.

`--chrome` with no granted tools is a supported, expected configuration — the browser's own tools
arrive with `--chrome`, so a call may drive a browser while `Bash` and the rest stay disabled. It is
not rejected.

Because argv is **built** from the resolved list rather than rewritten after the fact, a tool name
that collides with the value of another flag (`--model Bash`) cannot corrupt it. There is no scan and
nothing to mis-target.

> **Spec-internal fence — do not carry into consumer docs.** Building rather than rewriting, and
> asserting `--chrome` by count rather than position, are deliberate. The end-of-argv placement and
> the pairwise-removal algorithm in the consumer's wrapper were artefacts of rewriting a finished
> argv, not CLI requirements. Reintroducing either would restore a hazard this design removes.

## 5. `finish_reason`

Any `tool_use` maps to `stop`:

```python
finish_reason = "stop" if raw_stop_reason == "tool_use" else raw_stop_reason
```

Two independent grounds, both holding for every capability configuration:

1. **This provider never exposes tool calls.** No `tool_calls` array is ever populated. LiteLLM maps
   `finish_reason="tool_use"` to OpenAI's `"tool_calls"`, so emitting it hands a downstream
   tool-runner loop something it cannot honour — it errors on the missing array or spins.
2. **The ordinary cause is structured output.** When a schema was requested and the payload carries
   `structured_output`, the `tool_use` is the CLI's forced tool call implementing that schema. It is
   a completed turn and `stop` is simply correct.

Ground 1 does not depend on which tools are enabled, which makes it strictly stronger than the
`0.2.0` premise it replaces ("every tool is disabled, so structured output is the only possible
cause"). That premise is what capabilities invalidate; this one they cannot.

No information is lost. `provider_specific_fields["stop_reason"]` carries the CLI's raw value
unconditionally, as before, and `structured_output` is present exactly when the turn produced it — so
a caller distinguishes a completed structured turn from a truncated tool-use turn by the evidence
itself. A consumer treating `stop` with absent `structured_output` as invalid output, and repairing,
gets the right behaviour for free.

The SOUNDNESS comment is rewritten to state both grounds.

> **Spec-internal fence — do not carry into consumer docs.** A predicate of the form
> `schema_requested and structured_output is not None` was considered and rejected: with ground 1
> mapping the un-evidenced case to `stop` as well, such a condition cannot change any outcome. A
> condition that cannot alter the result would misrepresent the mapping as narrower than it is.

## 6. Testing

**Unit — argv.**

- `capabilities=None` produces an argv equal to a **hardcoded whole-argv literal** (the generated
  `--system-prompt-file` path substituted). This is a change detector: any change to default argv
  must be deliberate and update this test in the same commit. A baseline captured from an
  unconfigured instance is rejected — it compares one code path to itself and can only pass.
- Granted tools are absent from the disable pairs; ungranted tools remain, pairwise.
- A tool name equal to another flag's value (`--model Bash`) leaves argv uncorrupted. Structurally
  impossible by §4; retained as a regression pin against a future return to rewriting.
- `browser=True` emits `--chrome` exactly once; `browser=False` never emits it.
- Granting all ten emits no `--disallowed-tools` at all.

**Unit — validation.** An unknown name, a lowercase variant of a valid name, and a mix of valid and
invalid each raise `ValueError` naming the offender. Duplicates of a valid name do not raise and do
not change argv.

**Unit — `finish_reason`.** `tool_use` with `structured_output` → `stop`; `tool_use` without it →
`stop`, with the raw value still on `provider_specific_fields`; every other `stop_reason` passes
through unchanged.

**Live, behind the existing opt-in gate** (`RUN_LIVE_SMOKE=1` plus `claude` on PATH, matching
`tests/test_live_smoke.py`; skipped otherwise, never in CI — it bills a real subscription): one `-p` call with one tool granted (`Read` of a temp file is
the cheapest) and a trivial one-field schema, asserting `finish_reason == "stop"` and
`structured_output` present. This is the only test that can catch a wrong flag name or a CLI whose
behaviour with a tool enabled differs from the assumption in §5.

The consumer proved this shape against the real CLI before accepting its own live path. What is
ported is the **shape** of that probe, not a transcript: no observed `stop_reason` value is quoted
here, because none was available to quote.

## 7. Out of scope

No streaming. No new models. No change to any behaviour observable by a `capabilities=None` caller
other than §5's `finish_reason`, which is a strictly wider mapping of a value that was previously
unreachable for them. A `permission_mode` field on `Capabilities` was considered and deliberately
excluded from this release, because `tools` only removes the disable flag and the CLI's own
permission layer in headless mode is a separate concern from what this design governs.

## 8. Release

- Version `0.3.0` — additive. The `finish_reason` widening in §5 is the only behaviour change, and it
  affects a case a `capabilities=None` caller cannot produce.
- Tag `v0.3.0` on `master` **after** the squash-merge, per this repo's git convention — a tag made on
  the feature branch points off-`master`.
- `uv lock` re-run in the same commit as the version bump; the lockfile carries this package's own
  version.
- CHANGELOG entry naming `Capabilities` as pinned public surface, the validation contract (§3), and
  the `finish_reason` widening (§5).
- README gains a capabilities usage block; install pins move to `v0.3.0`.
