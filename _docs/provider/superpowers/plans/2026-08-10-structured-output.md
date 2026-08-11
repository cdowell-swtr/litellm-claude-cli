# Structured Output Implementation Plan (LCC3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let callers pass a JSON Schema through LiteLLM's standard `response_format` to `claude -p --json-schema`, and surface the CLI's parsed `structured_output` on the `ModelResponse`.

**Architecture:** All work lands in the single existing module `src/litellm_claude_cli/__init__.py`, which is deliberately self-contained (zero dependencies beyond `litellm`) — do not add imports from other packages and do not split the module. Two new module-private helpers (`_extract_json_schema`, `_encode_schema_arg`) sit beside the existing `_render_messages_to_prompt` / `_build_response` helpers; `_build_response` gains one keyword-only parameter; `_run` gains one parameter threaded from `completion`/`acompletion`.

**Tech Stack:** Python 3.12+, `litellm>=1.88.1` (verified against 1.89.0), pytest, ruff, mypy, uv.

**Source spec:** `_docs/provider/superpowers/specs/2026-08-10-structured-output-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Run tests with `.venv/bin/python -m pytest`**, not `.venv/bin/pytest` and not `uv run pytest`. The venv's console-script shebangs point at a stale path from a moved directory and fail with `exec: .../python: not found`. Fixing that is out of scope for this plan.
- **Never add `--bare` to argv.** It forces auth to `ANTHROPIC_API_KEY`/`apiKeyHelper` and never reads OAuth or the keychain, silently moving every call onto metered API billing.
- **Never remove `--system-prompt-file` or `--exclude-dynamic-system-prompt-sections`.** Without them a call pulls ~27k cache-creation and ~25k cache-read tokens of Claude Code's own harness prompt.
- **Never shrink `_DISABLED_TOOLS`.** One model turn per call is the contract, and Task 4's `finish_reason` mapping is sound only while every tool is disabled.
- **The three error kinds stay mutually distinguishable:** `ClaudeExhausted` (exhaustion), `RuntimeError` (malformed/other CLI error), `ValueError` (schema too large for argv). Consumers route all three differently.
- **`MAX_ARG_STRLEN` is `131072` bytes**, measured as UTF-8 length.
- **The module keeps zero dependencies beyond `litellm`.** No new imports except from the standard library.
- **Public pinned name is `structured_output`** — on the `ModelResponse` and inside `provider_specific_fields`. Do not rename it; a downstream consumer couples to it by name.
- Target version **`0.2.0`**, tag **`v0.2.0`**.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/litellm_claude_cli/__init__.py` | The whole provider | Modify — Tasks 1–4 |
| `tests/test_provider.py` | Unit tests against the injected runner | Modify — Tasks 1–4 |
| `tests/test_litellm_dispatch.py` | Offline proof the provider plugs into litellm | Modify — Task 5 |
| `tests/test_live_smoke.py` | Opt-in real-CLI tests | Modify — Task 6 |
| `CHANGELOG.md` | Release notes | Create — Task 7 |
| `README.md`, `pyproject.toml` | Docs + version | Modify — Task 7 |

---

### Task 1: Pass the schema to argv

Reads `response_format` out of `optional_params` and appends `--json-schema <compact JSON>` to argv. This alone makes schema-constrained output work, because the CLI's `result` field already carries the JSON as a string.

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` (add `_extract_json_schema`; thread `optional_params` through `completion`/`acompletion`/`_run`)
- Test: `tests/test_provider.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_extract_json_schema(optional_params: dict[str, Any] | None) -> dict[str, Any] | None`
  - `ClaudeCliLLM._run(self, model: str, messages: list[dict[str, Any]], pre_made_response: ModelResponse | None = None, optional_params: dict[str, Any] | None = None) -> ModelResponse`

- [x] **Step 1: Write the failing tests**

Add to `tests/test_provider.py`:

```python
def test_json_schema_reaches_argv() -> None:
    """response_format json_schema is passed as --json-schema <compact JSON>."""
    captured: dict[str, Any] = {}

    def _runner(argv: list[str], *, input_text: str | None) -> str:
        captured["argv"] = argv
        idx = argv.index("--system-prompt-file") + 1
        with open(argv[idx]) as fh:
            fh.read()
        return _fake_json_response()

    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
    llm = ClaudeCliLLM(runner=_runner)
    llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "v", "schema": schema},
            }
        },
    )

    argv = captured["argv"]
    assert "--json-schema" in argv, f"--json-schema missing from argv: {argv}"
    # The encoding must be compact and must immediately follow the flag.
    assert argv[argv.index("--json-schema") + 1] == json.dumps(schema, separators=(",", ":"))
    # Load-bearing invariants survive the structured path.
    assert "--system-prompt-file" in argv
    assert "--exclude-dynamic-system-prompt-sections" in argv
    assert "--disallowed-tools" in argv
    assert "--bare" not in argv


def test_no_json_schema_flag_without_response_format() -> None:
    """Absent response_format means no --json-schema in argv."""
    llm, captured = _make_llm_with_response(_fake_json_response())
    llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert "--json-schema" not in captured["argv"]


def test_json_object_response_format_passes_no_schema() -> None:
    """response_format {"type": "json_object"} carries no schema, so nothing is passed."""
    llm, captured = _make_llm_with_response(_fake_json_response())
    llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={"response_format": {"type": "json_object"}},
    )
    assert "--json-schema" not in captured["argv"]


def test_extract_json_schema_shapes() -> None:
    """_extract_json_schema returns the inner schema, or None for anything else."""
    schema = {"type": "object"}
    assert (
        _extract_json_schema(
            {"response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
        )
        == schema
    )
    assert _extract_json_schema(None) is None
    assert _extract_json_schema({}) is None
    assert _extract_json_schema({"response_format": {"type": "json_object"}}) is None
    assert _extract_json_schema({"response_format": "nonsense"}) is None
    assert _extract_json_schema({"response_format": {"type": "json_schema"}}) is None
    assert (
        _extract_json_schema({"response_format": {"type": "json_schema", "json_schema": {}}})
        is None
    )
```

Extend the import block at the top of `tests/test_provider.py` to include `_extract_json_schema`:

```python
from litellm_claude_cli import (
    ClaudeCliLLM,
    ClaudeExhausted,
    _build_response,
    _exhaustion_error,
    _extract_json_schema,
    _render_messages_to_prompt,
    register,
)
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider.py -k "json_schema or json_object" -v`

Expected: FAIL — `ImportError: cannot import name '_extract_json_schema'`.

- [x] **Step 3: Add the extraction helper**

In `src/litellm_claude_cli/__init__.py`, after `_exhaustion_error` and before `class _Runner`:

```python
def _extract_json_schema(
    optional_params: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the JSON Schema from an OpenAI-shaped ``response_format``, else ``None``.

    Handles ``{"type": "json_schema", "json_schema": {"schema": {...}}}``.  LiteLLM
    normalises a Pydantic ``response_format`` into exactly this shape before the
    provider sees it, so one shape covers both call styles.

    ``{"type": "json_object"}`` carries no schema, so there is nothing to pass to the
    CLI; it yields ``None`` rather than raising.
    """
    if not isinstance(optional_params, dict):
        return None
    response_format = optional_params.get("response_format")
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") != "json_schema":
        return None
    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return None
    schema = json_schema.get("schema")
    return schema if isinstance(schema, dict) else None
```

- [x] **Step 4: Thread `optional_params` through and build the flag**

In `ClaudeCliLLM`, replace both entry points so they forward `optional_params`:

```python
    def completion(self, *args: Any, **kwargs: Any) -> ModelResponse:  # noqa: D102
        model = kwargs.get("model") or (args[0] if args else "")
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else [])
        return self._run(
            model,
            messages,
            kwargs.get("model_response"),
            kwargs.get("optional_params"),
        )

    async def acompletion(self, *args: Any, **kwargs: Any) -> ModelResponse:  # noqa: D102
        model = kwargs.get("model") or (args[0] if args else "")
        messages = kwargs.get("messages") or (args[1] if len(args) > 1 else [])
        return self._run(
            model,
            messages,
            kwargs.get("model_response"),
            kwargs.get("optional_params"),
        )
```

Change the `_run` signature:

```python
    def _run(
        self,
        model: str,
        messages: list[dict[str, Any]],
        pre_made_response: ModelResponse | None = None,
        optional_params: dict[str, Any] | None = None,
    ) -> ModelResponse:
```

Immediately after `system_text, user_prompt = _render_messages_to_prompt(messages)`, add:

```python
        schema = _extract_json_schema(optional_params)
        schema_arg = (
            json.dumps(schema, separators=(",", ":")) if schema is not None else None
        )
```

Then inside the `try:` block, directly after the `argv = [...]` literal and **before** the
`for t in _DISABLED_TOOLS:` loop:

```python
            if schema_arg is not None:
                argv += ["--json-schema", schema_arg]
```

- [x] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`

Expected: PASS — the four new tests plus all pre-existing ones.

- [x] **Step 6: Lint and type-check**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy src`

Expected: all clean.

- [x] **Step 7: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_provider.py
git commit -m "feat(provider): pass response_format json_schema to claude --json-schema"
```

---

### Task 2: Guard the argv size ceiling

The schema goes in argv and the CLI has no file-path option for it, so `MAX_ARG_STRLEN` is reachable. Unguarded it surfaces as an opaque exec failure.

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` (add `_MAX_ARG_STRLEN`, `_encode_schema_arg`)
- Test: `tests/test_provider.py`

**Interfaces:**
- Consumes: `_extract_json_schema` from Task 1.
- Produces: `_MAX_ARG_STRLEN: int` (= `131072`), `_encode_schema_arg(schema: dict[str, Any]) -> str`.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_provider.py`:

```python
def _oversized_schema() -> dict[str, Any]:
    """A schema whose compact encoding exceeds MAX_ARG_STRLEN."""
    schema = {
        "type": "object",
        "properties": {
            f"k{i}": {"type": "string", "description": "d" * 200} for i in range(1000)
        },
    }
    assert len(json.dumps(schema, separators=(",", ":")).encode("utf-8")) > 131072
    return schema


def test_encode_schema_arg_rejects_oversized() -> None:
    """The guard names both the actual size and the ceiling."""
    with pytest.raises(ValueError, match=r"131072"):
        _encode_schema_arg(_oversized_schema())


def test_oversized_schema_raises_before_subprocess() -> None:
    """The guard fires before the runner is invoked — no temp file, no exec."""

    def _runner(argv: list[str], *, input_text: str | None) -> str:
        raise AssertionError("runner must not be reached when the schema is oversized")

    llm = ClaudeCliLLM(runner=_runner)
    with pytest.raises(ValueError):
        llm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "go"}],
            optional_params={
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "v", "schema": _oversized_schema()},
                }
            },
        )


def test_schema_just_under_ceiling_is_accepted() -> None:
    """A schema below the ceiling is encoded rather than rejected."""
    encoded = _encode_schema_arg({"type": "object", "title": "x" * 1000})
    assert len(encoded.encode("utf-8")) <= 131072
    assert encoded.startswith('{"type":"object"')
```

> Note for the implementer: `ValueError` is neither a `ClaudeExhausted` nor a `RuntimeError`
> by Python's own class hierarchy, so `pytest.raises(ValueError)` is the complete pin on the
> spec §7 taxonomy. Do not add `assert not isinstance(exc, RuntimeError)` — it asserts a
> language fact, not a property of this code.

Add `_encode_schema_arg` to the import block in `tests/test_provider.py`.

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider.py -k "oversized or ceiling or encode_schema" -v`

Expected: FAIL — `ImportError: cannot import name '_encode_schema_arg'`.

- [x] **Step 3: Add the constant and the guard**

In `src/litellm_claude_cli/__init__.py`, add next to the other module constants (after `_EXHAUSTION_MESSAGE`):

```python
# Linux caps a single argv element at MAX_ARG_STRLEN (128 KB).  The system prompt
# dodges this via --system-prompt-file, but the CLI accepts a JSON Schema ONLY as an
# inline argument, so a large schema is a reachable failure with no file fallback.
_MAX_ARG_STRLEN = 131072
```

Then add, directly after `_extract_json_schema`:

```python
def _encode_schema_arg(schema: dict[str, Any]) -> str:
    """Compactly encode *schema* for ``--json-schema``, refusing oversized input.

    Raises:
        ValueError: if the encoding exceeds :data:`_MAX_ARG_STRLEN`.  Deliberately
            neither :class:`ClaudeExhausted` nor :class:`RuntimeError` — callers route
            exhaustion, malformed output and caller error differently.
    """
    encoded = json.dumps(schema, separators=(",", ":"))
    size = len(encoded.encode("utf-8"))
    if size > _MAX_ARG_STRLEN:
        raise ValueError(
            f"JSON Schema is too large to pass to `claude --json-schema`: "
            f"{size} bytes exceeds the {_MAX_ARG_STRLEN}-byte MAX_ARG_STRLEN ceiling. "
            f"The CLI accepts the schema only as an inline argument, so there is no "
            f"file-based fallback — reduce the schema."
        )
    return encoded
```

- [x] **Step 4: Route `_run` through the guard**

In `_run`, replace the `schema_arg` assignment from Task 1 with:

```python
        schema = _extract_json_schema(optional_params)
        # Encode before the temp file is created so an oversized schema cannot leak one.
        schema_arg = _encode_schema_arg(schema) if schema is not None else None
```

- [x] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`

Expected: PASS.

- [x] **Step 6: Lint and type-check**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy src`

Expected: all clean.

- [x] **Step 7: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_provider.py
git commit -m "feat(provider): guard MAX_ARG_STRLEN ceiling on inline JSON Schema"
```

---

### Task 3: Surface `structured_output` on the response

Everything from here to Task 4 is change 2 — droppable as a unit if it turns awkward, because `message.content` already carries the JSON string.

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` (`_build_response`, and the `pre_made_response` branch of `_run`)
- Test: `tests/test_provider.py`

**Interfaces:**
- Consumes: `_run`'s `optional_params` threading from Task 1.
- Produces: `ModelResponse.structured_output` (attribute, present only when the CLI returned a non-null `structured_output`); `message.provider_specific_fields` containing `{"structured_output": ..., "stop_reason": ...}`, where `stop_reason` is always present.

- [x] **Step 1: Extend the test helper and write the failing tests**

Replace `_fake_json_response` in `tests/test_provider.py` with a version that can carry structured output. The `_MISSING` sentinel distinguishes "no key at all" from "key present but null" — the two cases behave identically downstream but must both be exercised:

```python
_MISSING = object()


def _fake_json_response(
    result: str = "[]",
    input_tokens: int = 10,
    output_tokens: int = 5,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    stop_reason: str = "end_turn",
    structured_output: Any = _MISSING,
) -> str:
    payload: dict[str, Any] = {
        "is_error": False,
        "result": result,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
        },
    }
    if structured_output is not _MISSING:
        payload["structured_output"] = structured_output
    return json.dumps(payload)
```

Then add these tests:

```python
def test_structured_output_surfaced_on_response() -> None:
    """The CLI's parsed object lands on ModelResponse.structured_output."""
    obj = {"a": "x", "n": 3}
    raw = _fake_json_response(
        result='{"a":"x","n":3}', stop_reason="tool_use", structured_output=obj
    )
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert resp.structured_output == obj
    # The JSON string stays in content so a caller's fallback stays honest.
    assert resp.choices[0].message.content == '{"a":"x","n":3}'


def test_structured_output_absent_when_cli_omits_it() -> None:
    """No structured_output key means the attribute is absent, not None."""
    llm, _ = _make_llm_with_response(_fake_json_response(result="plain text"))
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert not hasattr(resp, "structured_output"), (
        "attribute must be absent so getattr(resp, 'structured_output', None) is meaningful"
    )
    assert resp.choices[0].message.content == "plain text"


def test_structured_output_null_treated_as_absent() -> None:
    """A null structured_output must not surface as present-and-None."""
    llm, _ = _make_llm_with_response(
        _fake_json_response(result="plain text", structured_output=None)
    )
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert not hasattr(resp, "structured_output")


def test_provider_specific_fields_carry_object_and_raw_stop_reason() -> None:
    """provider_specific_fields carries the parsed object and the CLI's raw stop_reason."""
    obj = {"a": "x"}
    raw = _fake_json_response(
        result='{"a":"x"}', stop_reason="tool_use", structured_output=obj
    )
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    psf = resp.choices[0].message.provider_specific_fields
    assert psf["structured_output"] == obj
    assert psf["stop_reason"] == "tool_use"


def test_raw_stop_reason_surfaced_unconditionally() -> None:
    """The raw stop_reason is surfaced even on a plain, unstructured call."""
    llm, _ = _make_llm_with_response(_fake_json_response(stop_reason="end_turn"))
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    assert resp.choices[0].message.provider_specific_fields["stop_reason"] == "end_turn"


def test_structured_output_survives_pre_made_model_response() -> None:
    """litellm supplies its own ModelResponse; the attribute must land on THAT object."""
    obj = {"a": "x"}
    raw = _fake_json_response(
        result='{"a":"x"}', stop_reason="tool_use", structured_output=obj
    )
    llm, _ = _make_llm_with_response(raw)
    premade = litellm.ModelResponse()
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
        model_response=premade,
    )
    assert resp is premade
    assert resp.structured_output == obj
```

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider.py -k "structured or stop_reason or provider_specific" -v`

Expected: FAIL — `AttributeError: 'ModelResponse' object has no attribute 'structured_output'`.

- [x] **Step 3: Build the fields in `_build_response`**

In `src/litellm_claude_cli/__init__.py`, inside `_build_response`, replace this block:

```python
    text = (payload.get("result", "") or "").strip()
    stop_reason = payload.get("stop_reason") or "stop"
    u = payload.get("usage", {}) or {}
```

with:

```python
    text = (payload.get("result", "") or "").strip()
    raw_stop_reason = payload.get("stop_reason") or "stop"
    # A null structured_output is treated exactly as an absent one: the documented
    # contract is that the attribute is absent, never present-and-None.
    structured = payload.get("structured_output")
    u = payload.get("usage", {}) or {}
```

Then replace the `ModelResponse` construction and return:

```python
    # The CLI's own stop_reason is surfaced unconditionally — it is a pass-through of
    # what the CLI reported, and making it conditional would mean the case most worth
    # inspecting is the one that looks different.
    provider_specific_fields: dict[str, Any] = {"stop_reason": raw_stop_reason}
    if structured is not None:
        provider_specific_fields["structured_output"] = structured

    mr = ModelResponse(
        choices=[
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "provider_specific_fields": provider_specific_fields,
                },
                "finish_reason": raw_stop_reason,
            }
        ]
    )
    mr.usage = usage  # type: ignore[attr-defined]
    if structured is not None:
        mr.structured_output = structured  # type: ignore[attr-defined]
    return mr
```

- [x] **Step 4: Carry the attribute onto litellm's pre-made response**

In `_run`, replace the `pre_made_response` branch. Without this the attribute is silently
lost on the real `litellm.completion()` path, because litellm supplies its own
`ModelResponse` and that is what gets returned:

```python
        # If litellm passed a pre-made ModelResponse, populate it in-place.
        if pre_made_response is not None:
            pre_made_response.choices = result.choices
            pre_made_response.usage = result.usage  # type: ignore[attr-defined]
            structured = getattr(result, "structured_output", None)
            if structured is not None:
                pre_made_response.structured_output = structured  # type: ignore[attr-defined]
            return pre_made_response
```

- [x] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`

Expected: PASS. Note `test_handler_single_turn_system_via_file_prompt_via_stdin` and the other pre-existing tests must still pass unchanged.

- [x] **Step 6: Lint and type-check**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy src`

Expected: all clean.

- [x] **Step 7: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_provider.py
git commit -m "feat(provider): surface CLI structured_output on the ModelResponse"
```

---

### Task 4: Normalise `finish_reason` on the structured path

**Files:**
- Modify: `src/litellm_claude_cli/__init__.py` (`_build_response` signature + mapping; `_run` call site)
- Test: `tests/test_provider.py`

**Interfaces:**
- Consumes: `_build_response` as left by Task 3; `_run`'s `schema_arg` local from Task 1.
- Produces: `_build_response(raw: str, *, schema_requested: bool = False) -> ModelResponse`. The keyword is keyword-only and defaults to `False`, so the existing direct calls in `tests/test_provider.py` keep working unchanged.

- [x] **Step 1: Write the failing tests**

Add to `tests/test_provider.py`:

```python
def test_finish_reason_normalised_on_structured_path() -> None:
    """schema + tool_use maps to stop — tool_calls would lie about the message shape."""
    raw = _fake_json_response(
        result='{"a":"x"}', stop_reason="tool_use", structured_output={"a": "x"}
    )
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
    # Nothing is destroyed — the CLI's own value is one field away.
    assert resp.choices[0].message.provider_specific_fields["stop_reason"] == "tool_use"


def test_finish_reason_untouched_without_schema() -> None:
    """tool_use without a schema is NOT rewritten — no blanket remapping."""
    raw = _fake_json_response(result="x", stop_reason="tool_use")
    llm, _ = _make_llm_with_response(raw)
    resp = llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )
    # litellm's ModelResponse maps tool_use -> tool_calls; we leave that alone here.
    assert resp.choices[0].finish_reason == "tool_calls"


def test_finish_reason_untouched_for_other_stop_reasons() -> None:
    """A schema does not rewrite stop reasons other than tool_use."""
    raw = _fake_json_response(
        result='{"a":"x"}', stop_reason="max_tokens", structured_output={"a": "x"}
    )
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
    assert resp.choices[0].finish_reason == "length"
    assert resp.choices[0].message.provider_specific_fields["stop_reason"] == "max_tokens"
```

> Note for the implementer: `test_finish_reason_untouched_without_schema` and
> `test_finish_reason_untouched_for_other_stop_reasons` assert litellm's own
> normalisation (`tool_use`→`tool_calls`, `max_tokens`→`length`). If a litellm upgrade
> changes those mappings, update the expected value — do **not** add provider-side
> remapping to compensate. Only the schema + `tool_use` pair is ours to translate.

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_provider.py -k finish_reason -v`

Expected: FAIL — `test_finish_reason_normalised_on_structured_path` gets `tool_calls`, expected `stop`.

- [x] **Step 3: Add the mapping**

In `src/litellm_claude_cli/__init__.py`, change the `_build_response` signature:

```python
def _build_response(raw: str, *, schema_requested: bool = False) -> ModelResponse:
```

and extend its docstring with:

```
    Args:
        raw: the CLI's ``--output-format json`` payload.
        schema_requested: whether this call carried ``--json-schema``.  Gates the
            ``finish_reason`` normalisation below.
```

Then, immediately before the `provider_specific_fields` block added in Task 3, insert:

```python
    # `finish_reason` is a normalised interface field, not a provider passthrough.
    # `tool_calls` means "the caller must execute something and continue the loop",
    # and there is nothing here to execute: the tool call is only how the CLI
    # implements structured output, and no tool_calls array is exposed.  Left alone,
    # litellm maps the CLI's `tool_use` to `tool_calls` and a tool-runner loop either
    # errors on the missing array or spins.
    #
    # SOUNDNESS: this holds ONLY because _DISABLED_TOOLS disables every tool, so within
    # this provider `tool_use` has exactly one possible cause — the forced tool call
    # implementing structured output.  If the tool-disable list is ever relaxed, this
    # mapping stops being sound and must be revisited.
    finish_reason = raw_stop_reason
    if schema_requested and raw_stop_reason == "tool_use":
        finish_reason = "stop"
```

and change the `ModelResponse` construction to use it:

```python
                "finish_reason": finish_reason,
```

- [x] **Step 4: Pass the flag from `_run`**

In `_run`, replace `result = _build_response(raw)` with:

```python
        result = _build_response(raw, schema_requested=schema_arg is not None)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_provider.py -v`

Expected: PASS, all tests.

- [x] **Step 6: Lint and type-check**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy src`

Expected: all clean.

- [x] **Step 7: Commit**

```bash
git add src/litellm_claude_cli/__init__.py tests/test_provider.py
git commit -m "feat(provider): map tool_use to stop on the structured-output path"
```

---

### Task 5: Dispatch tests — prove survival, pin the limitation

The unit tests call `ClaudeCliLLM` directly. These go through real litellm, which is where the attribute can actually get dropped.

**Files:**
- Modify: `tests/test_litellm_dispatch.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4. Adds no production code.

- [x] **Step 1: Write the tests**

Add to `tests/test_litellm_dispatch.py`:

```python
_SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def _structured_runner(argv, *, input_text):
    """Records argv, and replies as the CLI does on the structured path."""
    _structured_runner.argv = argv  # type: ignore[attr-defined]
    return json.dumps(
        {
            "is_error": False,
            "stop_reason": "tool_use",
            "result": '{"a":"x"}',
            "structured_output": {"a": "x"},
            "usage": {"input_tokens": 5, "output_tokens": 7},
        }
    )


def test_completion_carries_schema_and_returns_structured_output():
    """Through litellm.completion: schema reaches argv, parsed object survives."""
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_structured_runner),
        }
    ]
    try:
        resp = litellm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "go"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "v", "schema": _SCHEMA},
            },
        )
    finally:
        litellm.custom_provider_map = saved

    argv = _structured_runner.argv
    assert "--json-schema" in argv
    assert argv[argv.index("--json-schema") + 1] == json.dumps(
        _SCHEMA, separators=(",", ":")
    )
    # This is the coupling a downstream consumer pins by name.
    assert resp.structured_output == {"a": "x"}
    assert resp.choices[0].message.content == '{"a":"x"}'
    assert resp.choices[0].finish_reason == "stop"


def test_pydantic_response_format_normalised_by_litellm():
    """A Pydantic response_format reaches the provider as the same json_schema dict."""
    from pydantic import BaseModel

    class Verdict(BaseModel):
        a: str

    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_structured_runner),
        }
    ]
    try:
        litellm.completion(
            model="claude-cli/claude-haiku-4-5-20251001",
            messages=[{"role": "user", "content": "go"}],
            response_format=Verdict,
        )
    finally:
        litellm.custom_provider_map = saved

    argv = _structured_runner.argv
    assert "--json-schema" in argv
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["properties"]["a"]["type"] == "string"


def test_anthropic_messages_drops_structured_output():
    """PINNED LIMITATION: the anthropic_messages transform discards the attribute.

    litellm.anthropic_messages() rebuilds the response as a fixed-key Anthropic dict,
    so neither ModelResponse.structured_output nor provider_specific_fields survives.
    A caller needing the parsed object must use litellm.completion(). The JSON string
    is still there in content. If a future litellm makes this pass, that is good news
    — update the assertion and the README/CHANGELOG note together.
    """
    saved = litellm.custom_provider_map
    litellm.custom_provider_map = [
        {
            "provider": "claude-cli",
            "custom_handler": ClaudeCliLLM(runner=_structured_runner),
        }
    ]
    try:
        out = _run(
            litellm.anthropic_messages(
                model="claude-cli/claude-haiku-4-5-20251001",
                max_tokens=64,
                messages=[{"role": "user", "content": "go"}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "v", "schema": _SCHEMA},
                },
            )
        )
    finally:
        litellm.custom_provider_map = saved

    # The schema still reaches the CLI on this path — only the return shape differs.
    assert "--json-schema" in _structured_runner.argv

    structured = (
        out.get("structured_output")
        if isinstance(out, dict)
        else getattr(out, "structured_output", None)
    )
    assert structured is None, "limitation changed — update README and CHANGELOG too"

    content = out["content"] if isinstance(out, dict) else out.content
    text = content[0]["text"] if isinstance(content[0], dict) else content[0].text
    assert text == '{"a":"x"}'
```

- [x] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_litellm_dispatch.py -v`

Expected: PASS, all four tests (one pre-existing plus three new).

- [x] **Step 3: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`

Expected: PASS, with only the live smoke test skipped.

- [x] **Step 4: Lint**

Run: `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`

Expected: clean.

- [x] **Step 5: Commit**

```bash
git add tests/test_litellm_dispatch.py
git commit -m "test(dispatch): prove structured_output survives completion(), pin anthropic_messages limit"
```

---

### Task 6: Live smoke test

Unit tests cannot catch a wrong flag name. Only a real CLI call can.

**Files:**
- Modify: `tests/test_live_smoke.py`

**Interfaces:**
- Consumes: Tasks 1–4. Adds no production code.

- [x] **Step 1: Write the test**

Add to `tests/test_live_smoke.py`:

```python
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
    assert obj is not None, "CLI returned no structured_output — check the --json-schema flag name"
    assert obj["colour"] in {"red", "green", "blue"}
    assert isinstance(obj["count"], int)
    # content carries the same JSON as a string.
    assert json.loads(resp.choices[0].message.content) == obj
    # The forced tool call must not leak as tool_calls.
    assert resp.choices[0].finish_reason == "stop"
    assert resp.choices[0].message.provider_specific_fields["stop_reason"] == "tool_use"
```

Add `import json` to the imports at the top of `tests/test_live_smoke.py`.

- [x] **Step 2: Confirm it is skipped by default**

Run: `.venv/bin/python -m pytest tests/test_live_smoke.py -v`

Expected: 2 skipped.

- [x] **Step 3: Run it live**

Run: `RUN_LIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_live_smoke.py -v`

Expected: 2 passed. This is the step that validates the flag name against the real CLI —
if `--json-schema` were wrong, every offline test would still pass and only this fails.

If `finish_reason` is not `stop` or `stop_reason` is not `tool_use`, **stop and report**:
the CLI's behaviour differs from what the spec recorded, and Task 4's mapping needs
revisiting rather than the assertion being loosened.

- [x] **Step 4: Commit**

```bash
git add tests/test_live_smoke.py
git commit -m "test(live): real --json-schema call asserts schema conformance"
```

---

### Task 7: Release 0.2.0

**Files:**
- Modify: `pyproject.toml:3` (version), `README.md`
- Create: `CHANGELOG.md`
- Modify: `PLAN.md`, `ACTION_LOG.md` (Planning Instrument, per `pi-convention.md`)

**Interfaces:**
- Consumes: Tasks 1–6.

- [x] **Step 1: Bump the version**

In `pyproject.toml`, change `version = "0.1.1"` to `version = "0.2.0"`.

- [x] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

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

`resp.choices[0].message.provider_specific_fields` carries the same parsed object plus
the CLI's raw `stop_reason`, surfaced on every call.

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
subject to Linux's `MAX_ARG_STRLEN`. A schema whose compact encoding exceeds 131072
bytes raises `ValueError` before any subprocess runs, rather than failing opaquely at
exec. This is distinct from `ClaudeExhausted` (exhaustion) and `RuntimeError`
(malformed output).

### Verified against

`claude` CLI 2.1.227, litellm 1.89.0. The declared floor remains `litellm>=1.88.1`.

## 0.1.1

- Ship `py.typed` marker (PEP 561).

## 0.1.0

- Initial release: `claude-cli/<model>` provider wrapping headless `claude -p`.
```

- [x] **Step 3: Update the README**

Change both install pins from `@v0.1.1` to `@v0.2.0`. Then add a `## Structured output`
section after the existing usage section:

````markdown
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
````

- [x] **Step 4: Update the Planning Instrument**

In `PLAN.md`, move `LCC3` into `Done`:

```markdown
## Next
_(nothing queued)_

## Done
- [x] LCC3 — Structured output via `--json-schema` + `structured_output` on the response → log:#0006
- [x] LCC2 — Register litellm-claude-cli in the patterns implementer registries (PI/MEMORY/Git/Docs-layout) → log:#0004
- [x] LCC1 — Adopt patterns conventions (PI, Committed Memory, Git, Docs-layout) → log:#0002
```

Append to `ACTION_LOG.md`:

```markdown
#### #0005 · inserted · LCC3 · 2026-08-10
Structured output through the provider, for the jsp scoring worker. Design spec at
`_docs/provider/superpowers/specs/2026-08-10-structured-output-design.md`, plan at
`_docs/provider/superpowers/plans/2026-08-10-structured-output.md`. Operational reason:
jsp needs schema-constrained JSON per scored criterion on the subscription, not metered API.

#### #0006 · completed · LCC3 · 2026-08-10
Shipped 0.2.0: `response_format` json_schema forwarded as `--json-schema`, CLI
`structured_output` surfaced on the `ModelResponse` under that name, `tool_use` mapped to
`stop` on the structured path only, and a `ValueError` guard on the MAX_ARG_STRLEN ceiling
the inline schema reintroduces. Deviation from the brief's suggestion: none needed —
`response_format` was verified to reach `CustomLLM` kwargs untransformed, so no bespoke
kwarg. Limitation pinned by test: `anthropic_messages()` drops the attribute.
```

- [x] **Step 5: Verify everything**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy src && bash hooks/docs-layout-check.sh`

Expected: tests pass with only live smoke skipped; lint, format, types and docs-layout all clean.

Also run the live suite once before tagging: `RUN_LIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_live_smoke.py -v`

- [x] **Step 6: Commit and tag**

```bash
git add pyproject.toml CHANGELOG.md README.md PLAN.md ACTION_LOG.md
git commit -m "release: 0.2.0 — structured output via --json-schema"
git tag -a v0.2.0 -m "0.2.0 — structured output: --json-schema in, structured_output out"
```

Do **not** push the tag without confirming with the repo owner first — the downstream
consumer installs by tag, so a tag is a published interface.

---

## Notes for the implementer

**Two spec questions were left open** and the plan implements the spec's stated default for
each. If the repo owner has since decided otherwise, these are the places to change:

1. **litellm floor.** The plan keeps `litellm>=1.88.1` while all verification was done on
   1.89.0. Raising the floor to `>=1.89.0` means one line in `pyproject.toml` and deleting
   the caveat paragraph in spec §2.
2. **Absent vs `None`.** `structured_output` is absent when there is no parsed object
   (Task 3). If it should instead always be present and `None`, change Task 3's two
   `if structured is not None:` guards and invert
   `test_structured_output_absent_when_cli_omits_it`.

**Tasks 3 and 4 are droppable as a unit.** They are change 2 from the brief. If either
turns awkward, Tasks 1, 2, 5 (minus its `structured_output` assertions), 6 and 7 still ship
a coherent 0.2.0 — the JSON arrives as a string in `message.content` either way.
