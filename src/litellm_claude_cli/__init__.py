"""LiteLLM CustomLLM provider that wraps headless ``claude -p``.

This module is self-contained — it has zero external dependencies beyond
``litellm``.  Do not add any imports from other packages.

The provider exposes a ``claude-cli/<model>`` namespace via LiteLLM's
``custom_provider_map`` mechanism, delegating each call to ``claude -p`` with
``--disable-slash-commands`` on every call.  What else a call may use is set by
an optional ``Capabilities``:

- ``exclusive=True`` passes an allowlist (``--tools``) and loads no MCP server
  (``--strict-mcp-config``): the call's tool set is exactly the grant.  This is
  the only mode that bounds the tool set.
- Otherwise (the default) the tools in ``_DISABLED_TOOLS`` are denied, less any
  granted back.  A deny list does not bound the tool set: every CLI release
  adds tools it does not name, and at least one of them (``Monitor``) runs
  shell commands.
- ``exclusive=True`` combined with ``browser=True`` attaches Claude in Chrome
  (``--chrome``) as a bounded allowlist of :data:`CLAUDE_IN_CHROME_TOOLS`
  instead, optionally with an MCP-based permission-prompt approver
  (``mcp_servers``, ``permission_prompt_tool``).  The call streams
  (``--output-format stream-json --verbose``).  The guarantee: no result is
  ever accepted without a ``system/init`` event that passed both checks (the
  offered tool set and ``permissionMode``), and a failing init is killed as
  soon as it is read — the outer fence on a browser session logged into real
  accounts.  This is checked as soon as init arrives, which the measured CLI
  emits before its first model request; it is not a guarantee that no request
  or tool call has already been dispatched by the time the kill lands.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess  # noqa: S404 — invoking the local `claude` CLI by fixed argv
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

import litellm
from litellm import CustomLLM, ModelResponse, Usage

# ---------------------------------------------------------------------------
# Constants (ported verbatim from backend.py)
# ---------------------------------------------------------------------------

# Tools denied on every non-exclusive call.  NOT a boundary: the CLI ships
# tools this list does not name (see the module docstring).  It is also the set
# of names a `Capabilities` may grant, in either mode.
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

# The `mcp__claude-in-chrome__*` tools offered under `--chrome` in
# browser+exclusive mode, measured 2026-09-27 against CLI 2.1.283 (LCC8). This
# is the inventory `Capabilities.tools` validates against in that mode, and
# the set `--disallowed-tools` subtracts from in its argv.
CLAUDE_IN_CHROME_TOOLS: tuple[str, ...] = tuple(
    f"mcp__claude-in-chrome__{name}"
    for name in (
        "browser_batch",
        "computer",
        "file_upload",
        "find",
        "form_input",
        "get_page_text",
        "gif_creator",
        "javascript_tool",
        "list_connected_browsers",
        "navigate",
        "read_console_messages",
        "read_network_requests",
        "read_page",
        "resize_window",
        "select_browser",
        "shortcuts_execute",
        "shortcuts_list",
        "switch_browser",
        "tabs_close_mcp",
        "tabs_context_mcp",
        "tabs_create_mcp",
        "upload_image",
    )
)
assert len(CLAUDE_IN_CHROME_TOOLS) == 22, (  # noqa: S101 — a module-load-time invariant, not test code
    "CLAUDE_IN_CHROME_TOOLS must hold exactly the 22 names measured 2026-09-27"
)

_MCP_SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class McpServer:
    """One MCP server to load via ``--mcp-config`` in browser+exclusive mode.

    Parameters
    ----------
    name:
        The server's key inside ``--mcp-config``'s ``mcpServers`` map.  Must
        match ``^[a-z][a-z0-9_]*$`` and be unique within a ``Capabilities``'
        ``mcp_servers``.  A ``Capabilities.permission_prompt_tool`` of the
        form ``mcp__<name>__<tool>`` must name a server present here.
    command:
        The executable to launch the server with.
    args:
        Its argv, excluding ``command`` itself.
    """

    name: str
    command: str
    args: tuple[str, ...]


@dataclass(frozen=True)
class Capabilities:
    """What a single ``claude -p`` call is permitted to touch.

    Parameters
    ----------
    tools:
        Tool names to ALLOW.  In every mode except browser+exclusive, valid
        names are exactly :data:`_DISABLED_TOOLS`; in browser+exclusive mode
        they are exactly :data:`CLAUDE_IN_CHROME_TOOLS`.  Anything else
        raises.  Matching is exact and case-sensitive, because neither mode
        can tell the caller about a bad name: subtractively, ``"bash"`` would
        leave ``Bash``'s disable flag in argv, and in an allowlist the CLI
        silently drops an unknown or wrong-case name, so the tool would be
        absent while the caller believed it granted.
    browser:
        Attach the browser with ``--chrome``.  The browser's own tools arrive
        with that flag, so ``browser=True`` carrying no ``tools`` is a coherent
        and supported configuration — a call may drive a browser while ``Bash``
        and the rest stay disabled.  Combined with ``exclusive=True``, see
        below.
    exclusive:
        ``tools`` is the WHOLE tool set rather than a subtraction from the
        deny list.  Without ``browser``: argv carries
        ``--tools <tools joined by ",">`` (in the caller's order) and
        ``--strict-mcp-config``, and no ``--disallowed-tools``.  ``tools=()``
        yields a call with no tools at all.  WITH ``browser=True``: the
        browser's own tools arrive as an MCP server under ``--chrome``, argv
        carries ``--tools ""`` plus ``--disallowed-tools`` for every
        :data:`CLAUDE_IN_CHROME_TOOLS` entry not in ``tools``, and the call's
        stream is read line by line with fail-closed ``system/init`` checks
        (see :class:`ClaudeCliLLM`). This is the only mode ``mcp_servers`` and
        ``permission_prompt_tool`` are meaningful in.
    mcp_servers:
        MCP servers to load via ``--mcp-config``.  Only valid with
        ``exclusive=True, browser=True``; empty in every other mode.
    permission_prompt_tool:
        The ``mcp__<name>__<tool>`` tool name the CLI's own permission prompt
        is routed to, via ``--permission-prompt-tool`` with an EXPLICIT
        ``--permission-mode default`` (never ``bypassPermissions``, and never
        a settings file's inherited default).  ``<name>`` must name a server
        in ``mcp_servers``.  Only valid with ``exclusive=True, browser=True``.
    """

    tools: tuple[str, ...] = ()
    browser: bool = False
    exclusive: bool = False
    mcp_servers: tuple[McpServer, ...] = ()
    permission_prompt_tool: str | None = None

    def __post_init__(self) -> None:
        browser_exclusive = self.exclusive and self.browser
        inventory = CLAUDE_IN_CHROME_TOOLS if browser_exclusive else _DISABLED_TOOLS
        unknown = tuple(t for t in self.tools if t not in inventory)
        if unknown:
            raise ValueError(
                "unknown tool name(s): "
                + ", ".join(repr(u) for u in unknown)
                + ". Valid names, matched exactly: "
                + ", ".join(inventory)
                + "."
            )

        if not browser_exclusive and (
            self.mcp_servers or self.permission_prompt_tool is not None
        ):
            raise ValueError(
                "mcp_servers and permission_prompt_tool require "
                "exclusive=True, browser=True; got "
                f"exclusive={self.exclusive!r}, browser={self.browser!r}."
            )

        seen: set[str] = set()
        for server in self.mcp_servers:
            if not _MCP_SERVER_NAME_RE.fullmatch(server.name):
                raise ValueError(
                    f"McpServer name {server.name!r} must match "
                    f"{_MCP_SERVER_NAME_RE.pattern!r}."
                )
            if server.name in seen:
                raise ValueError(f"duplicate McpServer name: {server.name!r}")
            seen.add(server.name)

        if self.permission_prompt_tool is not None:
            server_name = _mcp_server_name_prefix(self.permission_prompt_tool)
            if server_name is None or server_name not in seen:
                raise ValueError(
                    f"permission_prompt_tool {self.permission_prompt_tool!r} "
                    "must be of the form 'mcp__<name>__<tool>' where <name> "
                    f"is a server in mcp_servers; got server names {sorted(seen)!r}."
                )


def _mcp_server_name_prefix(tool: str) -> str | None:
    """Return the ``<name>`` in a ``mcp__<name>__<tool>`` tool name, or ``None``."""
    if not tool.startswith("mcp__"):
        return None
    name, sep, _rest = tool[len("mcp__") :].partition("__")
    return name if sep else None


def _disabled_tools_for(capabilities: Capabilities | None) -> tuple[str, ...]:
    """Resolve the tools to disable for one call.

    ``None`` returns :data:`_DISABLED_TOOLS` **itself**, so a caller that passes
    no capabilities gets the same ``--disallowed-tools`` flags as the build that
    predates capabilities — by construction, not by care.

    Emission order is always ``_DISABLED_TOOLS`` order, so the caller's tuple
    order is not observable in argv.
    """
    if capabilities is None:
        return _DISABLED_TOOLS
    granted = frozenset(capabilities.tools)
    return tuple(t for t in _DISABLED_TOOLS if t not in granted)


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

# Wall-clock ceiling for one `claude -p` subprocess, in seconds. 600 was the
# hardcoded value through v0.3.0, sized for judgement-shaped calls; agentic
# callers whose harness grants a longer lease pass their own `timeout=` at
# construction instead of living with this one.
DEFAULT_TIMEOUT = 600.0


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
        ValueError: if the encoding reaches or exceeds :data:`_MAX_ARG_STRLEN`.
            Deliberately neither :class:`ClaudeExhausted` nor :class:`RuntimeError`
            — callers route exhaustion, malformed output and caller error differently.
    """
    encoded = json.dumps(schema, separators=(",", ":"))
    size = len(encoded.encode("utf-8"))
    if size >= _MAX_ARG_STRLEN:
        raise ValueError(
            f"JSON Schema is too large to pass to `claude --json-schema`: "
            f"{size} bytes reaches the {_MAX_ARG_STRLEN}-byte MAX_ARG_STRLEN ceiling "
            f"(Linux's own check counts the NUL terminator, so this length is already "
            f"the first rejected one). The CLI accepts the schema only as an inline "
            f"argument, so there is no file-based fallback — reduce the schema."
        )
    return encoded


class _Runner(Protocol):
    """Protocol for the subprocess runner so mypy can type-check keyword-only ``input_text``."""

    def __call__(
        self, argv: list[str], *, input_text: str | None, timeout: float
    ) -> str: ...  # noqa: E704


def _default_runner(
    argv: list[str], *, input_text: str | None, timeout: float = DEFAULT_TIMEOUT
) -> str:
    """Run *argv* as a subprocess, passing *input_text* via stdin, killed at *timeout*."""
    proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
        argv,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        exhausted = _exhaustion_error(combined)
        if exhausted is not None:
            raise exhausted
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {combined.strip()}")
    return proc.stdout


# ---------------------------------------------------------------------------
# Streaming runner (browser+exclusive mode only)
# ---------------------------------------------------------------------------


class _StreamProcess(Protocol):
    """A running ``claude -p --chrome`` process, read line by line and
    killable mid-stream.

    ``kill()`` must be safe to call more than once and after the process has
    already exited on its own, and must leave no live descendant (LCC8 review
    F7: a naive single-pid kill leaves a grandchild running). ``finish()`` is
    called once the stream has been fully consumed (EOF, with no ``result``
    returned) to classify why: it must itself ensure the process is gone
    before returning, and its bounded stderr text feeds the same exhaustion
    classification the ``json``-mode runner uses. ``cleanup()`` is called
    exactly once by the caller, on every exit path (success or failure), to
    release anything ``finish()`` might otherwise leak if it's never called
    (LCC8 review N1: a captured-stderr temp file survived every path except
    the one through ``finish()``) — it must be safe to call whether or not
    ``finish()`` ran.
    """

    def __iter__(self) -> Iterator[str]: ...  # noqa: E704

    def kill(self) -> None: ...  # noqa: E704

    def reap(self, timeout: float = ...) -> None: ...  # noqa: E704

    @property
    def timed_out(self) -> bool: ...  # noqa: E704

    def finish(self) -> tuple[int | None, str]: ...  # noqa: E704

    def cleanup(self) -> None: ...  # noqa: E704


class _StreamRunner(Protocol):
    """Protocol for the streaming runner used only in browser+exclusive mode.

    Unlike :class:`_Runner`, this returns a live, killable, line-by-line
    :class:`_StreamProcess` rather than finished output, so the fail-closed
    ``system/init`` checks can kill it the moment a check fails."""

    def __call__(
        self, argv: list[str], *, input_text: str | None, timeout: float
    ) -> _StreamProcess: ...  # noqa: E704


# Bound on how much of a failed stream call's stderr is read for exhaustion
# classification (LCC8 review F6). Generous relative to any real CLI error.
_STREAM_STDERR_CAP = 65536


class _PopenStreamProcess:
    """The real :class:`_StreamProcess`, backed by :class:`subprocess.Popen`.

    Started in its own session (``start_new_session=True``) so ``kill()`` can
    signal the whole process group, not just the ``claude`` pid — an MCP
    server it spawned would otherwise survive (LCC8 review F7). A background
    timer kills the process if *timeout* elapses and records that the
    watchdog fired: the caller reads stdout lazily, so ``subprocess.run``'s
    own blocking timeout (used by :func:`_default_runner`) does not apply
    here, and the caller needs to tell a watchdog kill apart from any other
    reason the stream ended (LCC8 review F6).
    """

    def __init__(
        self, proc: subprocess.Popen[str], timeout: float, stderr_path: str
    ) -> None:
        self._proc = proc
        self._stderr_path: str | None = stderr_path
        self._timed_out = False
        self._timer = threading.Timer(timeout, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def _on_timeout(self) -> None:
        self._timed_out = True
        self.kill()

    def __iter__(self) -> Iterator[str]:
        assert self._proc.stdout is not None
        try:
            yield from self._proc.stdout
        finally:
            self._timer.cancel()

    def kill(self) -> None:
        self._timer.cancel()
        try:
            os.killpg(self._proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Not our own session leader (shouldn't happen given
            # start_new_session=True), or already reaped — fall back to a
            # direct signal so a real failure here is never silent.
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover — defensive only
            pass

    def reap(self, timeout: float = 5.0) -> None:
        """Wait up to *timeout* seconds for a clean exit; kill the group if it
        hasn't by then. Called on the success path so a result doesn't leave
        the process (or a lingering child) running (LCC8 review F7)."""
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.kill()

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def finish(self) -> tuple[int | None, str]:
        """Ensure the process is gone, then return ``(returncode, stderr)``.

        Called once the stream hits EOF without a ``result`` event, to
        classify why. ``kill()`` runs first (idempotent if already exited) so
        a lingering descendant holding stderr open can't block this read;
        stderr was captured to a temp file rather than a pipe for the same
        reason — reading a pipe here could deadlock behind a still-open
        write end.

        Does NOT delete the stderr temp file: the caller's ``cleanup()`` owns
        that, since ``finish()`` itself is never reached on the majority of
        exit paths (a passing result, or any fail-closed kill).
        """
        self.kill()
        stderr_text = ""
        if self._stderr_path is not None:
            try:
                with open(self._stderr_path, "rb") as fh:
                    stderr_text = fh.read(_STREAM_STDERR_CAP).decode("utf-8", "replace")
            except OSError:  # pragma: no cover — defensive only
                pass
        return self._proc.returncode, stderr_text

    def cleanup(self) -> None:
        """Delete the captured-stderr temp file. Idempotent, and safe whether
        or not ``finish()`` ran — the caller invokes this exactly once, on
        every exit path (LCC8 review N1)."""
        if self._stderr_path is None:
            return
        try:
            os.unlink(self._stderr_path)
        except OSError:
            pass
        self._stderr_path = None


def _default_stream_runner(
    argv: list[str],
    *,
    input_text: str | None,
    timeout: float = DEFAULT_TIMEOUT,
    stderr_dir: str | None = None,
) -> _StreamProcess:
    """Run *argv* as a subprocess, streaming stdout line by line.

    *input_text*, if given, is written to stdin and closed before any output
    is read (the CLI does not start emitting until stdin closes). Stderr is
    captured to a bounded temp file for exhaustion classification if the
    stream ends without a result (see :meth:`_PopenStreamProcess.finish`);
    the returned process's ``cleanup()`` deletes it on every exit path.
    *stderr_dir* places that temp file in a given directory instead of the
    platform default — not used by the provider itself, but lets a test
    inject an isolated directory to snapshot rather than sharing ``/tmp``
    with everything else on the machine. The process runs in its own session
    so it can be killed as a group. A background timer kills it after
    *timeout* seconds.
    """
    stderr_fd, stderr_path = tempfile.mkstemp(suffix=".stderr", dir=stderr_dir)
    try:
        with os.fdopen(stderr_fd, "wb") as stderr_file:
            proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                argv,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                start_new_session=True,  # LCC8 review F7: killable as a group
            )
    except BaseException:
        try:
            os.unlink(stderr_path)
        except OSError:
            pass
        raise
    if input_text is not None:
        assert proc.stdin is not None
        proc.stdin.write(input_text)
        proc.stdin.close()
    return _PopenStreamProcess(proc, timeout, stderr_path)


def _browser_exclusive_argv(capabilities: Capabilities) -> list[str]:
    """The argv block for ``exclusive=True, browser=True``, per seam v218 §C,
    amended by LCC8 review ruling F8.

    Order: ``--chrome``; ``--mcp-config <json>`` iff ``mcp_servers``;
    ``--strict-mcp-config``; ``--tools ""``; ``--permission-mode default``
    UNCONDITIONALLY (F8: never inherited from a settings file's own default,
    whether or not a prompt tool is set); ``--permission-prompt-tool <name>``
    iff ``permission_prompt_tool``; ``--disallowed-tools <t>`` for each
    :data:`CLAUDE_IN_CHROME_TOOLS` entry not granted, in inventory order.
    Never ``--permission-mode bypassPermissions``: bypass would never consult
    an approver, and F8 closes the gap where a call with no approver could
    otherwise inherit it.
    """
    argv: list[str] = ["--chrome"]
    if capabilities.mcp_servers:
        mcp_config = {
            "mcpServers": {
                server.name: {"command": server.command, "args": list(server.args)}
                for server in capabilities.mcp_servers
            }
        }
        argv += ["--mcp-config", json.dumps(mcp_config, separators=(",", ":"))]
    argv += ["--strict-mcp-config", "--tools", "", "--permission-mode", "default"]
    if capabilities.permission_prompt_tool is not None:
        argv += ["--permission-prompt-tool", capabilities.permission_prompt_tool]
    granted = frozenset(capabilities.tools)
    for t in CLAUDE_IN_CHROME_TOOLS:
        if t not in granted:
            argv += ["--disallowed-tools", t]
    return argv


def _read_stream_result(
    proc: _StreamProcess, capabilities: Capabilities, timeout: float
) -> str:
    """Read *proc* line by line, enforcing the fail-closed ``system/init`` checks.

    Returns the raw ``result`` event's JSON line, which ``_build_response``
    parses exactly as the ``json``-mode payload.

    **The guarantee:** no ``result`` is ever accepted without an ``init`` that
    passed both checks below, and a failing init (or any other failure) kills
    *proc* as soon as it is detected — not "before the first model turn": the
    CLI may already have dispatched a request or a tool call by the time the
    kill lands (see the module docstring and LCC8 review Q2).

    Checks, in the order the stream can violate them:
      - an ``assistant`` event must not arrive before any
        ``{"type":"system","subtype":"init"}`` event;
      - every such ``init`` event's ``tools`` must be present as a JSON array
        and equal ``set(capabilities.tools)`` exactly — a missing or ``null``
        ``tools`` key fails closed too, even when the grant is ``()`` (LCC8
        review F5: ``set(None or [])`` used to equal ``set(())`` and pass);
      - every such ``init`` event's ``permissionMode`` must be ``"default"``,
        UNCONDITIONALLY (LCC8 review F8: no longer gated on
        ``permission_prompt_tool`` being set, since argv now always emits
        ``--permission-mode default`` in this mode);
      - a ``result`` event must not arrive without a preceding ``init``;
      - the stream must yield a ``result`` at all.

    On EOF without a ``result``, ``proc.finish()`` classifies why: a fired
    watchdog raises ``subprocess.TimeoutExpired`` (matching every other
    mode's contract); a non-zero exit is checked for a subscription-exhaustion
    marker on stderr exactly as ``_default_runner`` checks combined
    stdout+stderr, raising ``ClaudeExhausted`` if it matches; otherwise a
    plain ``RuntimeError`` names the exit code, or that no init/result ever
    arrived.

    ANY exception out of this function — including a malformed, non-object
    stream line — kills *proc* before propagating (LCC8 review F1): the
    entire loop runs under one ``try/except BaseException: proc.kill(); raise``,
    so no exit path can disarm the watchdog and leave the child running
    unsupervised.
    """
    init_seen = False
    try:
        for raw_line in proc:
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                # RuntimeError, not the JSONDecodeError/ValueError this would
                # otherwise surface as: this module reserves ValueError for
                # caller error (see _encode_schema_arg), and _build_response
                # raises RuntimeError for the same situation in json mode.
                raise RuntimeError(
                    f"claude -p --chrome returned non-JSON stream output: {line[:200]!r}"
                ) from exc
            if not isinstance(event, dict):
                raise RuntimeError(
                    "claude -p --chrome: a stream line was not a JSON object: "
                    f"{line[:200]!r}"
                )
            etype = event.get("type")

            if etype == "assistant":
                if not init_seen:
                    raise RuntimeError(
                        "claude -p --chrome: an assistant event arrived before "
                        "any system/init event"
                    )
                continue

            if etype == "system" and event.get("subtype") == "init":
                init_seen = True
                offered = event.get("tools")
                if not isinstance(offered, list):
                    raise RuntimeError(
                        "claude -p --chrome: system/init 'tools' was missing "
                        f"or not a list: {offered!r}"
                    )
                got_tools = set(offered)
                want_tools = set(capabilities.tools)
                if got_tools != want_tools:
                    raise RuntimeError(
                        "claude -p --chrome: system/init offered tools "
                        f"{sorted(got_tools)}, expected exactly {sorted(want_tools)}"
                    )
                got_mode = event.get("permissionMode")
                if got_mode != "default":
                    raise RuntimeError(
                        "claude -p --chrome: system/init permissionMode is "
                        f"{got_mode!r}, expected 'default' (this mode always "
                        "requires it explicitly, so a settings file's own "
                        "default can never take over)"
                    )
                continue

            if etype == "result":
                if not init_seen:
                    raise RuntimeError(
                        "claude -p --chrome: a result event arrived without "
                        "any preceding system/init event"
                    )
                proc.reap()
                return line

            # Anything else (hook_started, hook_response, thinking_tokens,
            # rate_limit_event, user, ...) carries no information either
            # check needs, and is ignored.

        # EOF without a result: classify why before raising.
        returncode, stderr_text = proc.finish()
        if proc.timed_out:
            raise subprocess.TimeoutExpired(
                cmd="claude -p --chrome",
                timeout=timeout,
                output=None,
                stderr=stderr_text,
            )
        if returncode not in (0, None):
            exhausted = _exhaustion_error(stderr_text)
            if exhausted is not None:
                raise exhausted
            raise RuntimeError(
                f"claude -p --chrome failed ({returncode}): {stderr_text.strip()}"
            )
        if not init_seen:
            raise RuntimeError(
                "claude -p --chrome: the stream ended without a system/init event"
            )
        raise RuntimeError(
            "claude -p --chrome: the stream ended without a result event"
        )
    except BaseException:
        proc.kill()
        raise
    finally:
        # Owns the stderr temp file's lifetime end to end: every exit path
        # above — the return, and every raise, including ones the
        # except-clause re-raises — passes through here exactly once
        # (LCC8 review N1).
        proc.cleanup()


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

    Args:
        raw: the CLI's ``--output-format json`` payload.

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
    # Top-level `usage` is the call's own model alone.  A tool that drives a
    # second model (WebSearch/WebFetch run on haiku) reports only in
    # `modelUsage`, so that is summed when present — its entry for the call's
    # own model is the breakdown top-level `usage` summarises, so it wins.
    # `thinkingTokens` is not summed: measured, `outputTokens` already
    # includes it.
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        entries = [e for e in model_usage.values() if isinstance(e, dict)]
        prompt_toks = sum(e.get("inputTokens", 0) or 0 for e in entries)
        completion_toks = sum(e.get("outputTokens", 0) or 0 for e in entries)
        cache_read = sum(e.get("cacheReadInputTokens", 0) or 0 for e in entries)
        cache_creation = sum(e.get("cacheCreationInputTokens", 0) or 0 for e in entries)
    else:
        model_usage = None
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

    # The CLI's own stop_reason is surfaced unconditionally — it is a pass-through of
    # what the CLI reported, and making it conditional would mean the case most worth
    # inspecting is the one that looks different.
    provider_specific_fields: dict[str, Any] = {"stop_reason": raw_stop_reason}
    if structured is not None:
        provider_specific_fields["structured_output"] = structured
    # The per-model breakdown behind `usage`, verbatim, for a caller that
    # attributes spend per model rather than taking the sum.
    if model_usage is not None:
        provider_specific_fields["model_usage"] = model_usage

    mr = ModelResponse(
        choices=[
            {
                "message": {
                    "role": "assistant",
                    "content": text,
                    "provider_specific_fields": provider_specific_fields,
                },
                "finish_reason": finish_reason,
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
        the real subprocess runner.  Override in tests.  Not used in
        browser+exclusive mode, which uses ``stream_runner`` instead.
    capabilities:
        What the call may touch.  ``None`` (the default) disables the ten tools
        in :data:`_DISABLED_TOOLS` and grants no browser.  Tools outside that
        list (``Monitor``, ``TaskCreate``, MCP tools, ...) are not disabled by
        it; only ``Capabilities(exclusive=True)`` bounds the tool set.
        ``Skill`` is closed separately by ``--disable-slash-commands``, which is
        fixed argv and not governed by this parameter.
    timeout:
        Wall-clock seconds one ``claude -p`` subprocess may run before it is
        killed.  Defaults to :data:`DEFAULT_TIMEOUT` (600, the hardcoded value
        through v0.3.0).  Callers whose own scheduling grants a call more time
        — a lease, a queue TTL — pass that budget here so the two agree; the
        subprocess kill is `subprocess.TimeoutExpired`, exactly as before.
        Must be a positive number; anything else raises ``ValueError`` at
        construction, not at call time.
    stream_runner:
        Callable with signature ``(argv, *, input_text, timeout) ->
        _StreamProcess``, used ONLY when ``capabilities.exclusive and
        capabilities.browser``.  Defaults to the real streaming subprocess
        runner, which starts the child in its own session so a failing check
        can kill the whole process group, not just the ``claude`` pid.
        Override in tests with a fake driven by a recorded stream, and assert
        ``kill()`` was called when a check should fail closed — or, to prove
        the actuator itself rather than just the call to it, point this at
        the real runner with a fake CLI script and assert the process (and
        any child it spawned) is actually dead afterward.
    """

    def __init__(
        self,
        runner: _Runner = _default_runner,
        capabilities: Capabilities | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        stream_runner: _StreamRunner = _default_stream_runner,
    ) -> None:
        super().__init__()
        if (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise ValueError(
                f"timeout must be a positive number of seconds, got {timeout!r}"
            )
        self._runner = runner
        self._capabilities = capabilities
        self._timeout = float(timeout)
        self._stream_runner = stream_runner

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

        capabilities = self._capabilities
        browser_exclusive = (
            capabilities is not None and capabilities.exclusive and capabilities.browser
        )

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
                # Skills are unusable on this path — a one-shot call resolves no
                # slash command — and their listing is injected into every call's
                # context.  The flag also closes the `Skill` tool, which sits
                # outside `_DISABLED_TOOLS`.
                "--disable-slash-commands",
            ]
            if browser_exclusive:
                # This mode's fail-closed init checks (`_read_stream_result`)
                # need the stream, not a finished JSON blob.
                argv += ["--output-format", "stream-json", "--verbose"]
            else:
                argv += ["--output-format", "json"]
            argv += ["--model", bare_model]
            if schema_arg is not None:
                argv += ["--json-schema", schema_arg]
            # The capability block: everything governing what this call may
            # touch.  `--chrome`'s position within argv is not significant to
            # the CLI; it sits here so the block reads as a unit.
            if browser_exclusive:
                # browser_exclusive implies capabilities is not None; narrows for mypy.
                assert capabilities is not None
                argv += _browser_exclusive_argv(capabilities)
            elif capabilities is not None and capabilities.exclusive:
                # An allowlist makes deny flags redundant; emitting them would
                # re-couple argv to a list known to be incomplete.
                argv += [
                    "--tools",
                    ",".join(capabilities.tools),
                    "--strict-mcp-config",
                ]
            else:
                if capabilities is not None and capabilities.browser:
                    argv.append("--chrome")
                for t in _disabled_tools_for(capabilities):
                    argv += ["--disallowed-tools", t]

            if browser_exclusive:
                assert capabilities is not None  # narrows for mypy
                proc = self._stream_runner(
                    argv, input_text=user_prompt, timeout=self._timeout
                )
                raw = _read_stream_result(proc, capabilities, self._timeout)
            else:
                raw = self._runner(argv, input_text=user_prompt, timeout=self._timeout)
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
