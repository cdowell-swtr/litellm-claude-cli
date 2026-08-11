"""LiteLLM CustomLLM provider that wraps headless ``claude -p``.

This module is self-contained — it has zero external dependencies beyond
``litellm``.  Do not add any imports from other packages.

The provider exposes a ``claude-cli/<model>`` namespace via LiteLLM's
``custom_provider_map`` mechanism, delegating each call to ``claude -p`` with
all agentic tools disabled so every call is exactly one model turn.
"""

from __future__ import annotations

import json
import os
import re
import subprocess  # noqa: S404 — invoking the local `claude` CLI by fixed argv
import tempfile
from typing import Any, Protocol

import litellm
from litellm import CustomLLM, ModelResponse, Usage

# ---------------------------------------------------------------------------
# Constants (ported verbatim from backend.py)
# ---------------------------------------------------------------------------

# Tools disabled on every call so `claude -p` returns exactly ONE model turn.
_DISABLED_TOOLS = (
    "Bash",
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Task",
    "NotebookEdit",
)

# Substrings marking usage-limit / subscription-exhaustion in `claude -p` output.
_EXHAUSTION_MARKERS = (
    "usage limit",
    "rate limit reached",
    "quota",
    "limit reached",
    "session limit",
)

_EXHAUSTION_MESSAGE = "claude subscription usage limit reached"

# Linux caps a single argv element at MAX_ARG_STRLEN (128 KB).  The system prompt
# dodges this via --system-prompt-file, but the CLI accepts a JSON Schema ONLY as an
# inline argument, so a large schema is a reachable failure with no file fallback.
_MAX_ARG_STRLEN = 131072


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ClaudeExhausted(Exception):
    """Raised when ``claude -p`` signals subscription exhaustion.

    Carries an optional ``reset_hint`` extracted from the CLI output.
    This is a module-local type — the backend seam maps it to
    ``BackendExhausted`` in a later task.
    """

    def __init__(self, message: str, *, reset_hint: str | None = None) -> None:
        super().__init__(message)
        self.reset_hint = reset_hint


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _exhaustion_error(text: str) -> ClaudeExhausted | None:
    """Return a :class:`ClaudeExhausted` if *text* signals subscription exhaustion.

    Extracts any ``resets …`` hint from the text.  Returns ``None`` if the text
    does not match any exhaustion marker.
    """
    if not any(m in text.lower() for m in _EXHAUSTION_MARKERS):
        return None
    m = re.search(r"resets[^\"}\n]*", text, re.IGNORECASE)
    hint = m.group(0).strip().rstrip(".") if m else None
    msg = _EXHAUSTION_MESSAGE + (f" — {hint}" if hint else "")
    return ClaudeExhausted(msg, reset_hint=hint)


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


class _Runner(Protocol):
    """Protocol for the subprocess runner so mypy can type-check keyword-only ``input_text``."""

    def __call__(self, argv: list[str], *, input_text: str | None) -> str: ...  # noqa: E704


def _default_runner(argv: list[str], *, input_text: str | None) -> str:
    """Run *argv* as a subprocess, passing *input_text* via stdin."""
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        exhausted = _exhaustion_error(combined)
        if exhausted is not None:
            raise exhausted
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {combined.strip()}")
    return proc.stdout


def _flatten_content(content: Any) -> str:
    """Flatten a content value (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                # Other block types (image, etc.) are silently skipped.
            else:
                # object with .type / .text attrs
                btype = getattr(block, "type", None)
                if btype == "text":
                    parts.append(getattr(block, "text", "") or "")
        # Join with blank lines to match the original backend's system rendering
        # (and _render_messages_to_prompt's own system_parts join) — the engine
        # supplies multiple self-labeled cache_control blocks (diff, context, prompt).
        return "\n\n".join(parts)
    return str(content) if content else ""


def _render_messages_to_prompt(
    messages: list[dict[str, Any]],
) -> tuple[str, str]:
    """Convert an OpenAI-shaped messages list to ``(system_text, user_prompt_text)``.

    All ``role=="system"`` messages are joined for the system file.
    All remaining messages are rendered as a transcript for stdin.

    Message shapes handled:

    - ``role=="system"``: joined into the system text.
    - ``role=="tool"``: rendered as ``[tool_result]\\n<text>``.
    - ``role=="assistant"`` with ``tool_calls``: rendered per call as
      ``[assistant tool_call] <name> <arguments>``.
    - Otherwise: ``[<role>] <text>``.

    Content may be a ``str`` or a list of ``{"type":"text","text":...}`` blocks.
    """
    system_parts: list[str] = []
    prompt_parts: list[str] = []

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            system_parts.append(_flatten_content(content))
            continue

        if role == "tool":
            text = _flatten_content(content)
            prompt_parts.append(f"[tool_result]\n{text}")
            continue

        # Assistant messages may carry tool_calls (OpenAI tool-call shape)
        tool_calls = msg.get("tool_calls")
        if role == "assistant" and tool_calls:
            for call in tool_calls:
                fn = call.get("function", {}) if isinstance(call, dict) else {}
                name = fn.get("name", "")
                arguments = fn.get("arguments", "{}")
                prompt_parts.append(f"[assistant tool_call] {name} {arguments}")
            continue

        # User messages: pass raw text (no prefix); other roles: prefix with role.
        text = _flatten_content(content)
        if text:
            if role == "user":
                prompt_parts.append(text)
            else:
                prompt_parts.append(f"[{role}] {text}")

    system_text = "\n\n".join(system_parts)
    user_prompt_text = "\n\n".join(prompt_parts)
    return system_text, user_prompt_text


def _build_response(raw: str) -> ModelResponse:
    """Parse the JSON output from ``claude -p`` into a :class:`ModelResponse`.

    Raises:
        RuntimeError: if *raw* is not valid JSON, not a dict, or signals an
            error that is not subscription exhaustion.
        ClaudeExhausted: if the error payload signals subscription exhaustion.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude -p returned non-JSON output: {raw[:120]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise RuntimeError(
            f"claude -p returned unexpected JSON type: {type(payload).__name__}"
        )

    if payload.get("is_error"):
        result = payload.get("result") or ""
        exhausted = _exhaustion_error(result)
        if exhausted is not None:
            raise exhausted
        raise RuntimeError(f"claude -p error: {payload.get('result')}")

    text = (payload.get("result", "") or "").strip()
    raw_stop_reason = payload.get("stop_reason") or "stop"
    # A null structured_output is treated exactly as an absent one: the documented
    # contract is that the attribute is absent, never present-and-None.
    structured = payload.get("structured_output")
    u = payload.get("usage", {}) or {}

    cache_read = u.get("cache_read_input_tokens", 0) or 0
    cache_creation = u.get("cache_creation_input_tokens", 0) or 0
    prompt_toks = u.get("input_tokens", 0) or 0
    completion_toks = u.get("output_tokens", 0) or 0
    # litellm's Usage accepts extra **params for vendor-specific fields.
    usage = Usage(  # type: ignore[call-arg]
        prompt_tokens=prompt_toks,
        completion_tokens=completion_toks,
        total_tokens=prompt_toks + completion_toks,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )

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


# ---------------------------------------------------------------------------
# CustomLLM subclass
# ---------------------------------------------------------------------------


class ClaudeCliLLM(CustomLLM):
    """LiteLLM :class:`~litellm.CustomLLM` that delegates to ``claude -p``.

    Parameters
    ----------
    runner:
        Callable with signature ``(argv, *, input_text) -> str``.  Defaults to
        the real subprocess runner.  Override in tests.
    """

    def __init__(
        self,
        runner: _Runner = _default_runner,
    ) -> None:
        super().__init__()
        self._runner = runner

    # Both overrides use *args/**kwargs because callers (litellm internals AND
    # our direct unit tests) pass very different subsets of the base signature.
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

    def _run(
        self,
        model: str,
        messages: list[dict[str, Any]],
        pre_made_response: ModelResponse | None = None,
        optional_params: dict[str, Any] | None = None,
    ) -> ModelResponse:
        # Strip provider prefix defensively (litellm auto-strips, but be safe).
        bare_model = model.removeprefix("claude-cli/")

        system_text, user_prompt = _render_messages_to_prompt(messages)
        schema = _extract_json_schema(optional_params)
        # Encode before the temp file is created so an oversized schema cannot leak one.
        schema_arg = _encode_schema_arg(schema) if schema is not None else None

        # Write system content to a temp file (mode 0o600) so it never appears
        # as an argv element.  Linux's MAX_ARG_STRLEN (~128 KB) rejects large
        # per-argument strings; bundle-agent system blocks regularly exceed that.
        fd, sys_path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(system_text)
            os.chmod(sys_path, 0o600)  # noqa: S103 — temp file; owner-read-only is correct

            argv = [
                "claude",
                "-p",
                "--system-prompt-file",
                sys_path,
                "--exclude-dynamic-system-prompt-sections",
                "--output-format",
                "json",
                "--model",
                bare_model,
            ]
            if schema_arg is not None:
                argv += ["--json-schema", schema_arg]
            for t in _DISABLED_TOOLS:
                argv += ["--disallowed-tools", t]

            raw = self._runner(argv, input_text=user_prompt)
        finally:
            try:
                os.unlink(sys_path)
            except OSError:
                pass

        result = _build_response(raw)

        # If litellm passed a pre-made ModelResponse, populate it in-place.
        if pre_made_response is not None:
            pre_made_response.choices = result.choices
            pre_made_response.usage = result.usage  # type: ignore[attr-defined]
            structured = getattr(result, "structured_output", None)
            if structured is not None:
                pre_made_response.structured_output = structured  # type: ignore[attr-defined]
            return pre_made_response

        return result


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register() -> None:
    """Idempotently add ``claude-cli`` to ``litellm.custom_provider_map``.

    Safe to call multiple times; existing ``claude-cli`` entries are replaced
    rather than duplicated.
    """
    handler = ClaudeCliLLM()
    # Filter out any existing claude-cli entry, then prepend the fresh one.
    existing = [
        p
        for p in (litellm.custom_provider_map or [])
        if p.get("provider") != "claude-cli"
    ]
    litellm.custom_provider_map = [
        {"provider": "claude-cli", "custom_handler": handler},
        *existing,
    ]
