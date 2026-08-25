# First-class capabilities (v0.3.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, validated `Capabilities` parameter to `ClaudeCliLLM` so a caller can enable specific tools and attach the browser, and re-key the `tool_use` → `stop` mapping onto a ground that capabilities cannot invalidate.

**Architecture:** A frozen `Capabilities` dataclass validates tool names at construction. A pure `_disabled_tools_for()` resolves the disable list, returning `_DISABLED_TOOLS` itself when capabilities are absent so the default argv is byte-identical by construction. argv is **built** from that resolved list rather than rewritten after the fact. `finish_reason` maps `tool_use` → `stop` unconditionally, because this provider never populates a `tool_calls` array.

**Tech Stack:** Python ≥3.12, `litellm>=1.88.1`, pytest, ruff, mypy, uv.

**Spec:** `_docs/provider/superpowers/specs/2026-08-24-capabilities-design.md`

## Global Constraints

- Python `>=3.12`; dependency floor `litellm>=1.88.1` — do not raise it.
- `src/litellm_claude_cli/__init__.py` has **zero external dependencies beyond `litellm`** (module docstring, line 3). Stdlib imports are fine; `dataclasses` is stdlib.
- The package is a single module. Do not split it.
- Lint/type gates, all must pass: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run pytest -q`.
- Ruff default line length (88).
- Tool names match **exactly and case-sensitively**. The valid set is exactly `_DISABLED_TOOLS`.
- `provider_specific_fields["stop_reason"]` carries the CLI's raw value **unconditionally** — never make it conditional.
- Commits follow Conventional Commits (enforced by `conventional-pre-commit`).

---

### Task 1: `Capabilities` type and its validation

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` (add import at line 13 block; add class after the `_DISABLED_TOOLS` constant, which ends at line 39)
- Test: `tests/test_capabilities.py` (create)

**Interfaces:**
- Consumes: `_DISABLED_TOOLS` (existing module constant, a 10-tuple).
- Produces: `Capabilities(tools: tuple[str, ...] = (), browser: bool = False)`, frozen dataclass, importable as `from litellm_claude_cli import Capabilities`. Raises `ValueError` from `__post_init__` on any name not in `_DISABLED_TOOLS`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_capabilities.py`:

```python
"""`Capabilities` construction, validation, and the argv it produces."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from litellm_claude_cli import _DISABLED_TOOLS, Capabilities, ClaudeCliLLM


def _fake_json_response(result: str = "ok") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": result,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def _make_llm(
    capabilities: Capabilities | None = None,
) -> tuple[ClaudeCliLLM, dict[str, Any]]:
    """A provider whose runner captures argv instead of executing anything."""
    captured: dict[str, Any] = {"argv": None}

    def _runner(argv: list[str], *, input_text: str | None) -> str:
        captured["argv"] = argv
        return _fake_json_response()

    return ClaudeCliLLM(runner=_runner, capabilities=capabilities), captured


def test_capabilities_defaults_are_empty_and_browserless() -> None:
    cap = Capabilities()
    assert cap.tools == ()
    assert cap.browser is False


def test_capabilities_accepts_every_disabled_tool_name() -> None:
    cap = Capabilities(tools=_DISABLED_TOOLS)
    assert cap.tools == _DISABLED_TOOLS


def test_capabilities_is_frozen() -> None:
    cap = Capabilities(tools=("Bash",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        cap.tools = ("Read",)  # type: ignore[misc]


def test_capabilities_rejects_unknown_tool_name() -> None:
    with pytest.raises(ValueError, match="Nonesuch"):
        Capabilities(tools=("Nonesuch",))


def test_capabilities_rejects_lowercase_variant_of_valid_name() -> None:
    """Matching is exact: `bash` would silently leave Bash's disable flag in argv."""
    with pytest.raises(ValueError, match="bash"):
        Capabilities(tools=("bash",))


def test_capabilities_error_names_every_offender_and_the_valid_set() -> None:
    with pytest.raises(ValueError) as excinfo:
        Capabilities(tools=("Bash", "Nope", "alsobad"))
    msg = str(excinfo.value)
    assert "Nope" in msg
    assert "alsobad" in msg
    assert "Bash" in msg  # named as part of the valid set
    assert "WebFetch" in msg


def test_capabilities_allows_duplicate_valid_names() -> None:
    """Set semantics: a duplicate is a valid name twice, which is harmless."""
    assert Capabilities(tools=("Bash", "Bash")).tools == ("Bash", "Bash")
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py -q`
Expected: FAIL — `ImportError: cannot import name 'Capabilities' from 'litellm_claude_cli'`

- [x] **Step 3: Add the import**

In `src/litellm_claude_cli/__init__.py`, in the stdlib import block (currently `import json` / `os` / `re` / `subprocess` / `tempfile`), add in alphabetical position:

```python
from dataclasses import dataclass
```

It goes after `import tempfile` and before `from typing import Any, Protocol`.

- [x] **Step 4: Add the class immediately after the `_DISABLED_TOOLS` tuple**

`_DISABLED_TOOLS` ends with `)` on line 39. Insert below it (before the `_EXHAUSTION_MARKERS` comment):

```python


@dataclass(frozen=True)
class Capabilities:
    """What a single ``claude -p`` call is permitted to touch.

    Parameters
    ----------
    tools:
        Tool names to ALLOW.  Each is subtracted from the disable list, so the
        valid names are exactly :data:`_DISABLED_TOOLS` — anything else is
        either already enabled (making the grant meaningless) or a typo, and
        both raise.  Matching is exact and case-sensitive: accepting ``"bash"``
        would leave ``Bash``'s disable flag in argv while the caller believed
        the tool was enabled.
    browser:
        Attach the browser with ``--chrome``.  The browser's own tools arrive
        with that flag, so ``browser=True`` carrying no ``tools`` is a coherent
        and supported configuration — a call may drive a browser while ``Bash``
        and the rest stay disabled.
    """

    tools: tuple[str, ...] = ()
    browser: bool = False

    def __post_init__(self) -> None:
        unknown = tuple(t for t in self.tools if t not in _DISABLED_TOOLS)
        if unknown:
            raise ValueError(
                "unknown tool name(s): "
                + ", ".join(repr(u) for u in unknown)
                + ". Valid names, matched exactly: "
                + ", ".join(_DISABLED_TOOLS)
                + "."
            )
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py -q`
Expected: PASS (7 tests)

- [x] **Step 6: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: all pass, no existing test broken.

- [x] **Step 7: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_capabilities.py
git commit -m "feat: add validated Capabilities type (LCC4)"
```

---

### Task 2: `_disabled_tools_for` — resolving the disable list

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` (add function directly below the `Capabilities` class from Task 1)
- Test: `tests/test_capabilities.py` (append)

**Interfaces:**
- Consumes: `Capabilities` and `_DISABLED_TOOLS` from Task 1.
- Produces: `_disabled_tools_for(capabilities: Capabilities | None) -> tuple[str, ...]`. Returns the `_DISABLED_TOOLS` object itself when given `None`.

- [x] **Step 1: Write the failing tests**

First amend the existing import line at the top of `tests/test_capabilities.py` — a
mid-file import would fail `ruff check` under `E402`:

```python
from litellm_claude_cli import (
    _DISABLED_TOOLS,
    _disabled_tools_for,
    Capabilities,
    ClaudeCliLLM,
)
```

Then append the tests:

```python
def test_disabled_tools_for_none_is_the_constant_itself() -> None:
    """Identity, not equality: the default path is the same object, so the
    default argv cannot drift from the no-capabilities build."""
    assert _disabled_tools_for(None) is _DISABLED_TOOLS


def test_disabled_tools_for_empty_capabilities_disables_everything() -> None:
    assert _disabled_tools_for(Capabilities()) == _DISABLED_TOOLS


def test_disabled_tools_for_subtracts_granted_names() -> None:
    resolved = _disabled_tools_for(Capabilities(tools=("Bash", "Read")))
    assert "Bash" not in resolved
    assert "Read" not in resolved
    assert "Write" in resolved
    assert len(resolved) == len(_DISABLED_TOOLS) - 2


def test_disabled_tools_for_granting_all_leaves_nothing_disabled() -> None:
    assert _disabled_tools_for(Capabilities(tools=_DISABLED_TOOLS)) == ()


def test_disabled_tools_for_preserves_provider_order_not_caller_order() -> None:
    """Caller tuple order is not observable: two Capabilities granting the same
    tools must resolve identically."""
    a = _disabled_tools_for(Capabilities(tools=("Read", "Bash")))
    b = _disabled_tools_for(Capabilities(tools=("Bash", "Read")))
    assert a == b
    assert list(a) == [t for t in _DISABLED_TOOLS if t not in {"Bash", "Read"}]


def test_disabled_tools_for_ignores_duplicates() -> None:
    once = _disabled_tools_for(Capabilities(tools=("Bash",)))
    twice = _disabled_tools_for(Capabilities(tools=("Bash", "Bash")))
    assert once == twice
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py -q`
Expected: FAIL — `ImportError: cannot import name '_disabled_tools_for'`

- [x] **Step 3: Write the implementation**

Directly below the `Capabilities` class:

```python


def _disabled_tools_for(capabilities: Capabilities | None) -> tuple[str, ...]:
    """Resolve the tools to disable for one call.

    ``None`` returns :data:`_DISABLED_TOOLS` **itself**, so a caller that passes
    no capabilities gets argv byte-identical to the build that predates them —
    by construction, not by care.

    Emission order is always ``_DISABLED_TOOLS`` order, so the caller's tuple
    order is not observable in argv.
    """
    if capabilities is None:
        return _DISABLED_TOOLS
    granted = frozenset(capabilities.tools)
    return tuple(t for t in _DISABLED_TOOLS if t not in granted)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py -q`
Expected: PASS (13 tests)

- [x] **Step 5: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_capabilities.py
git commit -m "feat: resolve the disable list from Capabilities (LCC4)"
```

---

### Task 3: Wire capabilities into `ClaudeCliLLM` and argv

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` — constructor (lines 344-359: docstring + `__init__`), argv build (lines 410-421)
- Test: `tests/test_capabilities.py` (append)

**Interfaces:**
- Consumes: `_disabled_tools_for` (Task 2), `Capabilities` (Task 1).
- Produces: `ClaudeCliLLM(runner=..., capabilities: Capabilities | None = None)`, storing `self._capabilities`. argv gains an optional `--chrome` immediately before the `--disallowed-tools` pairs.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_capabilities.py`:

```python
def _disallowed(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--disallowed-tools"]


def _call(llm: ClaudeCliLLM, model: str = "claude-cli/claude-haiku-4-5-20251001") -> None:
    llm.completion(
        model=model,
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )


def test_capabilities_none_leaves_the_real_argv_untouched() -> None:
    """THE load-bearing default.  A hardcoded whole-argv literal, not a captured
    baseline: a baseline would compare one code path to itself and could only
    pass.  Any change to default argv must be deliberate and update this test in
    the same commit."""
    llm, captured = _make_llm(None)
    _call(llm)
    argv = captured["argv"]
    sys_path = argv[argv.index("--system-prompt-file") + 1]
    assert argv == [
        "claude",
        "-p",
        "--system-prompt-file",
        sys_path,
        "--exclude-dynamic-system-prompt-sections",
        "--output-format",
        "json",
        "--model",
        "claude-haiku-4-5-20251001",
        "--disallowed-tools",
        "Bash",
        "--disallowed-tools",
        "Read",
        "--disallowed-tools",
        "Edit",
        "--disallowed-tools",
        "Write",
        "--disallowed-tools",
        "Grep",
        "--disallowed-tools",
        "Glob",
        "--disallowed-tools",
        "WebFetch",
        "--disallowed-tools",
        "WebSearch",
        "--disallowed-tools",
        "Task",
        "--disallowed-tools",
        "NotebookEdit",
    ]


def test_empty_capabilities_produces_the_same_argv_as_none() -> None:
    none_llm, none_cap = _make_llm(None)
    _call(none_llm)
    empty_llm, empty_cap = _make_llm(Capabilities())
    _call(empty_llm)
    # The temp system-prompt file differs per call; compare everything else.
    def _scrub(argv: list[str]) -> list[str]:
        out = list(argv)
        out[out.index("--system-prompt-file") + 1] = "<TMP>"
        return out

    assert _scrub(none_cap["argv"]) == _scrub(empty_cap["argv"])


def test_granted_tools_are_absent_from_disallowed_pairs() -> None:
    llm, captured = _make_llm(Capabilities(tools=("Bash", "Read")))
    _call(llm)
    values = _disallowed(captured["argv"])
    assert "Bash" not in values
    assert "Read" not in values
    assert "Write" in values
    assert len(values) == len(_DISABLED_TOOLS) - 2


def test_granting_every_tool_emits_no_disallowed_flag_at_all() -> None:
    llm, captured = _make_llm(Capabilities(tools=_DISABLED_TOOLS))
    _call(llm)
    assert "--disallowed-tools" not in captured["argv"]


def test_a_tool_name_equal_to_the_model_value_never_corrupts_argv() -> None:
    """Structurally impossible now that argv is BUILT rather than rewritten —
    there is no scan to mis-target.  Retained as a regression pin against any
    future return to rewriting a finished argv."""
    llm, captured = _make_llm(Capabilities(tools=("Bash",)))
    _call(llm, model="claude-cli/Bash")
    argv = captured["argv"]
    assert argv[argv.index("--model") + 1] == "Bash"
    values = _disallowed(argv)
    assert "Bash" not in values
    assert len(values) == len(_DISABLED_TOOLS) - 1


def test_browser_true_emits_chrome_exactly_once() -> None:
    llm, captured = _make_llm(Capabilities(browser=True))
    _call(llm)
    assert captured["argv"].count("--chrome") == 1


def test_browser_false_never_emits_chrome() -> None:
    llm, captured = _make_llm(Capabilities(tools=("Bash",)))
    _call(llm)
    assert "--chrome" not in captured["argv"]


def test_browser_with_no_granted_tools_is_supported() -> None:
    """A common live shape: the browser's own tools arrive with --chrome, so the
    model may drive a browser while every other tool stays disabled."""
    llm, captured = _make_llm(Capabilities(browser=True))
    _call(llm)
    argv = captured["argv"]
    assert "--chrome" in argv
    assert _disallowed(argv) == list(_DISABLED_TOOLS)


def test_chrome_and_grants_compose() -> None:
    llm, captured = _make_llm(Capabilities(tools=("Bash", "Read"), browser=True))
    _call(llm)
    argv = captured["argv"]
    assert argv.count("--chrome") == 1
    values = _disallowed(argv)
    assert "Bash" not in values and "Read" not in values
    assert len(values) == len(_DISABLED_TOOLS) - 2
```

- [x] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py -q`
Expected: FAIL — `TypeError: ClaudeCliLLM.__init__() got an unexpected keyword argument 'capabilities'`

- [x] **Step 3: Add the constructor parameter**

Replace the constructor docstring tail and `__init__` (currently lines 344-359). The existing docstring ends with the `runner:` paragraph; add a `capabilities:` paragraph after it, then the new signature:

```python
    capabilities:
        What the call may touch.  ``None`` (the default) disables every tool,
        producing argv byte-identical to the build that predates this
        parameter.
    """

    def __init__(
        self,
        runner: _Runner = _default_runner,
        capabilities: Capabilities | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._capabilities = capabilities
```

- [x] **Step 4: Build argv from the resolved list**

In `_run`, replace the existing disable loop (currently lines 420-421):

```python
            for t in _DISABLED_TOOLS:
                argv += ["--disallowed-tools", t]
```

with the capability block — `--chrome` first, so everything governing what the call may touch reads as one unit:

```python
            # The capability block: everything governing what this call may
            # touch.  `--chrome`'s position within argv is not significant to
            # the CLI; it sits here so the block reads as a unit.
            if self._capabilities is not None and self._capabilities.browser:
                argv.append("--chrome")
            for t in _disabled_tools_for(self._capabilities):
                argv += ["--disallowed-tools", t]
```

- [x] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py -q`
Expected: PASS (22 tests)

- [x] **Step 6: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: all pass. `tests/test_provider.py` is unaffected by this task — every existing argv test runs with `capabilities=None`.

- [x] **Step 7: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_capabilities.py
git commit -m "feat: build argv from capabilities (LCC4)"
```

---

### Task 4: Re-key `finish_reason` onto a capability-independent ground

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` — `_build_response` signature (line 247), its docstring (line 252), the SOUNDNESS comment and mapping (lines 300-312), the call site (line 430)
- Modify: `tests/test_provider.py` — `test_finish_reason_untouched_without_schema` (lines 677-686), `test_disabled_tools_all_reach_argv_as_disallowed` docstring and assertion message (lines 712-745)
- Test: `tests/test_provider.py` (append two tests)

**Interfaces:**
- Consumes: nothing from Tasks 1-3 at runtime. `_build_response`'s `schema_requested` keyword is **removed**; the only caller is `_run` (line 430).
- Produces: `_build_response(raw: str) -> ModelResponse` — mapping any `tool_use` to `stop`.

**Note on the two existing tests this task changes.** `test_finish_reason_untouched_without_schema` currently asserts `finish_reason == "tool_calls"` and documents "no blanket remapping". That is precisely the behaviour the spec replaces, so the test inverts rather than being deleted — its own comment ("litellm's ModelResponse maps tool_use -> tool_calls") is the evidence for why. `test_disabled_tools_all_reach_argv_as_disallowed` keeps its hardcoded ten and all its assertions; only its *stated rationale* changes, because it currently justifies itself by the premise being retired.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_provider.py`, after `test_finish_reason_untouched_for_other_stop_reasons`:

```python
def test_finish_reason_tool_use_maps_to_stop_without_a_schema() -> None:
    """`tool_use` maps to `stop` on a ground independent of any schema: this
    provider never populates a `tool_calls` array, so litellm's tool_use ->
    tool_calls mapping would hand a downstream tool-runner loop something it
    cannot honour.  Nothing a caller enables can make this provider expose a
    tool call."""
    raw = _fake_json_response(result="x", stop_reason="tool_use")
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.provider_specific_fields["stop_reason"] == "tool_use"


def test_truncated_tool_use_is_distinguishable_by_evidence_not_finish_reason() -> None:
    """A completed structured turn and a truncated tool-use turn both report
    `stop`; the caller tells them apart by `structured_output`'s presence, which
    is the evidence itself."""
    raw = _fake_json_response(result="x", stop_reason="tool_use")
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "v", "schema": {"type": "object"}},
            }
        },
    )
    assert resp.choices[0].finish_reason == "stop"
    assert "structured_output" not in resp.choices[0].message.provider_specific_fields
    assert not hasattr(resp, "structured_output")
```

- [x] **Step 2: Invert the test that pins the retired behaviour**

In `tests/test_provider.py`, replace `test_finish_reason_untouched_without_schema` (lines 677-686) entirely with:

```python
def test_finish_reason_never_emits_tool_calls() -> None:
    """Was `test_finish_reason_untouched_without_schema`, which pinned "no
    blanket remapping".  That rule rested on every tool being disabled, which
    `Capabilities` retires.  The mapping is now unconditional and this test
    pins the consequence: `tool_calls` must never reach a caller, because no
    `tool_calls` array is ever populated to back it."""
    raw = _fake_json_response(result="x", stop_reason="tool_use")
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert resp.choices[0].finish_reason != "tool_calls"
    assert resp.choices[0].finish_reason == "stop"
```

- [x] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_provider.py -q -k finish_reason`
Expected: FAIL — the three tests above report `tool_calls` where `stop` is asserted.

- [x] **Step 4: Make the mapping unconditional**

In `_build_response`, replace the SOUNDNESS comment and mapping (lines 306-312) with:

```python
    # SOUNDNESS: `tool_use` maps to `stop` unconditionally, on two independent
    # grounds, both of which hold for every `Capabilities` configuration.
    #   1. This provider never populates a `tool_calls` array.  litellm maps
    #      `finish_reason="tool_use"` to OpenAI's `"tool_calls"`, so emitting it
    #      hands a downstream tool-runner loop something it cannot honour — it
    #      errors on the missing array or spins.  Nothing a caller can enable
    #      makes this provider expose a tool call, so this ground is independent
    #      of capabilities.
    #   2. The ordinary cause is structured output: when a schema was requested
    #      and the payload carries `structured_output`, the `tool_use` IS the
    #      CLI's forced tool call implementing that schema — a completed turn,
    #      for which `stop` is simply correct.
    # This replaces 0.2.0's premise ("every tool is disabled, so structured
    # output is the only possible cause of tool_use"), which enabling a tool
    # invalidates.  Ground 1 is strictly stronger: capabilities cannot reach it.
    # No information is lost — the CLI's raw value is surfaced unconditionally
    # below, and `structured_output` is present exactly when the turn produced
    # one, so a caller distinguishes a completed structured turn from a
    # truncated tool-use turn by the evidence itself.
    finish_reason = "stop" if raw_stop_reason == "tool_use" else raw_stop_reason
```

- [x] **Step 5: Remove the now-unused `schema_requested` parameter**

Line 247 becomes:

```python
def _build_response(raw: str) -> ModelResponse:
```

Delete the `schema_requested:` paragraph from its docstring (line 252 and its continuation). Line 430 becomes:

```python
        result = _build_response(raw)
```

- [x] **Step 6: Update the tripwire test's rationale**

In `tests/test_provider.py`, `test_disabled_tools_all_reach_argv_as_disallowed` keeps every assertion and the hardcoded ten. Replace only its docstring (lines 713-727) with:

```python
    """Pins `_DISABLED_TOOLS` to a hardcoded list, and pins argv wiring to that list.

    Two things rest on this tuple. It is the basis of the one-model-turn
    invariant: every tool disabled means a call cannot run an agentic loop. And
    it is the set of names `Capabilities.tools` validates against — a grant
    outside it raises, so shrinking this tuple silently narrows what callers may
    ask for.

    If this test derived its expectation from `_DISABLED_TOOLS` itself, shrinking
    (or renaming an entry in) the tuple would shrink both sides of the comparison
    in lockstep and the test would stay green while the invariant silently broke
    — which is exactly what happened before this test was rewritten (deleting
    "NotebookEdit" from the tuple left the old version passing).
    `expected_disabled_tools` below is therefore written out independently, as
    literal strings, so that ANY change to `_DISABLED_TOOLS` — shrink, add,
    reorder, rename — fails this test and forces the author to look at this
    comment first.

    When the CLI gains an executable tool, this tuple should GROW to preserve the
    invariant; subtraction in `_disabled_tools_for` keeps its meaning unchanged.
    The known consumer keeps its own copy of this expectation, so a change here
    reddens its suite too and gets re-read by a person on both sides.
    """
```

And replace the assertion message (lines 741-746) with:

```python
    assert _DISABLED_TOOLS == expected_disabled_tools, (
        "_DISABLED_TOOLS has drifted from the hardcoded expectation in this test. "
        "This list is the one-model-turn invariant AND the set of names "
        "Capabilities.tools accepts. If this change is intentional, update the "
        "tuple here AND check both — do not just fix this assertion."
    )
```

- [x] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_provider.py tests/test_litellm_dispatch.py -q`
Expected: PASS. `test_finish_reason_normalised_on_structured_path` and `test_finish_reason_untouched_for_other_stop_reasons` still pass unchanged — the first is ground 2, the second is a non-`tool_use` value.

- [x] **Step 8: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: all pass.

- [x] **Step 9: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_provider.py
git commit -m "fix: re-key tool_use -> stop onto a capability-independent ground (LCC4)"
```

---

### Task 5: Live opt-in proof against the real CLI

**Files:**
- Modify: `tests/test_live_smoke.py` (append)

**Interfaces:**
- Consumes: `Capabilities`, `ClaudeCliLLM` (Tasks 1-3); the unconditional mapping (Task 4).
- Produces: nothing other tasks consume.

**Note.** This ports the *shape* of the consumer's probe, not a transcript. No observed `stop_reason` value is asserted, because none was available to quote — the test asserts the two facts that matter and would catch a wrong flag name or a CLI whose behaviour with a tool enabled differs from Task 4's assumption. Unit tests cannot catch either.

- [x] **Step 1: Write the test**

Append to `tests/test_live_smoke.py`:

```python
@pytest.mark.skipif(
    os.environ.get("RUN_LIVE_SMOKE") != "1" or shutil.which("claude") is None,
    reason="live: set RUN_LIVE_SMOKE=1 with the `claude` CLI on PATH",
)
def test_live_granted_tool_with_schema_finishes_as_stop(tmp_path):
    """One real `-p` call with a tool enabled AND a schema.

    The only check that can catch a wrong flag name, or a CLI whose behaviour
    with a tool enabled differs from the `tool_use` -> `stop` reasoning in
    `_build_response`.  Read is the cheapest tool to grant: the model reads one
    small local file and answers from it.
    """
    from litellm_claude_cli import Capabilities

    target = tmp_path / "colour.txt"
    target.write_text("The colour is teal.\n")

    llm = ClaudeCliLLM(capabilities=Capabilities(tools=("Read",)))
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[
            {
                "role": "user",
                "content": f"Read the file {target} and report the colour it names.",
            }
        ],
        optional_params={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "colour",
                    "schema": {
                        "type": "object",
                        "properties": {"colour": {"type": "string"}},
                        "required": ["colour"],
                        "additionalProperties": False,
                    },
                },
            }
        },
    )

    assert resp.choices[0].finish_reason == "stop"
    structured = resp.choices[0].message.provider_specific_fields["structured_output"]
    assert "colour" in structured
```

- [x] **Step 2: Verify it skips by default**

Run: `uv run pytest tests/test_live_smoke.py -q`
Expected: skipped (unless `RUN_LIVE_SMOKE=1` is already set) — it bills a real subscription call and must never run in CI.

- [x] **Step 3: Run it for real, once**

Run: `RUN_LIVE_SMOKE=1 uv run pytest tests/test_live_smoke.py -q -k granted_tool`
Expected: PASS.

**If it fails, stop and report — do not adjust the assertion to match.** A failure here is the empirical finding this test exists to produce: it means the real CLI's behaviour with a tool enabled differs from Task 4's reasoning, and the spec's §5 needs revisiting before release.

- [x] **Step 4: Commit**

```bash
git add tests/test_live_smoke.py
git commit -m "test: live proof of finish_reason with a granted tool (LCC4)"
```

---

### Task 6: Release 0.3.0

**Files:**
- Modify: `pyproject.toml:3`, `uv.lock`, `CHANGELOG.md`, `README.md`
- Modify: `PLAN.md`, `ACTION_LOG.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the `v0.3.0` tag the known consumer pins.

- [x] **Step 1: Bump the version**

`pyproject.toml` line 3: `version = "0.2.0"` → `version = "0.3.0"`

- [x] **Step 2: Re-lock**

Run: `uv lock`
Expected: `uv.lock` updates. The lockfile carries this package's own version and goes stale on every release; it must move in the same commit as the bump.

- [x] **Step 3: Add the CHANGELOG entry**

Add a `## 0.3.0` section above `## 0.2.0`, matching the existing file's style, covering:
- **Added** — `Capabilities(tools=..., browser=...)`, an optional `ClaudeCliLLM` parameter. Pinned public surface. `tools` names are subtracted from the disable list and validated against it; an unknown or wrong-case name raises `ValueError`. `browser=True` appends `--chrome`, and is supported with no granted tools.
- **Changed** — `finish_reason`: `tool_use` now maps to `stop` unconditionally, not only on the structured path. This provider never populates a `tool_calls` array, so `tool_calls` could never be honoured by a caller. The CLI's raw value is unchanged in `provider_specific_fields["stop_reason"]`. A caller passing no capabilities cannot produce the newly-affected case.
- **Unchanged** — argv is byte-identical for `capabilities=None`, pinned by test.

- [x] **Step 4: Update the README**

Add a capabilities usage block after the structured-output block:

```python
from litellm_claude_cli import Capabilities, ClaudeCliLLM

llm = ClaudeCliLLM(capabilities=Capabilities(tools=("Read", "Grep"), browser=True))
```

Note that every other tool stays disabled, that names are validated against the disable list and must match exactly, and that omitting `capabilities` disables everything as before. Move the install pins from `v0.2.0` to `v0.3.0`.

- [x] **Step 5: Run the full gate**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`
Expected: all pass.

- [x] **Step 6: Tick the plan and log**

In `PLAN.md`, move `LCC4` from `Next` to `Done` as:
`- [x] LCC4 — First-class capabilities: `Capabilities` param, argv built from it, `tool_use`→`stop` re-keyed (v0.3.0) → log:#0008`

Append `#### #0008 · completed · LCC4 · <date>` to `ACTION_LOG.md`, recording what shipped, the two existing tests whose rationale changed and why, and the live-test result.

- [x] **Step 7: Commit and open the PR**

```bash
git add -A
git commit -m "chore: release 0.3.0 (LCC4)"
git push -u origin LCC4-capabilities
gh pr create --fill
```

- [x] **Step 8: Tag AFTER the squash-merge**

Once the PR is squash-merged, tag `master` — **not** the feature branch. A tag made on the branch points at a commit that is not on `master` after a squash-merge, and the consumer pins by tag.

```bash
git checkout master && git pull
git tag v0.3.0 && git push origin v0.3.0
```
