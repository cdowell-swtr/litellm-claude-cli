"""Browser+exclusive mode (LCC8, seam v218 §C): capabilities, argv, and the
fail-closed `system/init` stream checks.

TDD: written before the implementation. The recorded stream fixture is a
redacted copy of a real `claude -p --chrome --mcp-config <approver>
--permission-prompt-tool mcp__approver__approve ...` run (CLI 2.1.283,
2026-09-27); see `fixtures/browser_exclusive_approver_run.jsonl`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from litellm_claude_cli import (
    CLAUDE_IN_CHROME_TOOLS,
    Capabilities,
    ClaudeCliLLM,
    ClaudeExhausted,
    McpServer,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "browser_exclusive_approver_run.jsonl"
)

# The six tools the recorded run's `system/init` event actually offered.
FIXTURE_TOOLS = (
    "mcp__claude-in-chrome__javascript_tool",
    "mcp__claude-in-chrome__navigate",
    "mcp__claude-in-chrome__select_browser",
    "mcp__claude-in-chrome__tabs_close_mcp",
    "mcp__claude-in-chrome__tabs_context_mcp",
    "mcp__claude-in-chrome__tabs_create_mcp",
)


def _fixture_lines() -> list[str]:
    return FIXTURE_PATH.read_text().splitlines()


class _FakeStreamProcess:
    """A `_StreamProcess` double over a fixed list of lines.

    `finish_returncode`/`finish_stderr` configure what `finish()` reports for
    the EOF-without-a-result path (LCC8 review F6); `mark_timed_out()` lets a
    test simulate the watchdog having fired before EOF."""

    def __init__(
        self,
        lines: list[str],
        finish_returncode: int | None = 0,
        finish_stderr: str = "",
    ) -> None:
        self._lines = lines
        self.killed = False
        self.reaped = False
        self.cleaned_up = False
        self._timed_out = False
        self._finish_returncode = finish_returncode
        self._finish_stderr = finish_stderr

    def __iter__(self):
        return iter(self._lines)

    def kill(self) -> None:
        self.killed = True

    def reap(self, timeout: float = 5.0) -> None:
        self.reaped = True

    def cleanup(self) -> None:
        self.cleaned_up = True

    def mark_timed_out(self) -> None:
        self._timed_out = True

    @property
    def timed_out(self) -> bool:
        return self._timed_out

    def finish(self) -> tuple[int | None, str]:
        return self._finish_returncode, self._finish_stderr


_MISSING_TOOLS = object()


def _init_line(tools: Any = (), permission_mode: str = "default") -> str:
    """Build a `system/init` line. `tools=None` emits `"tools": null`;
    `tools=_MISSING_TOOLS` omits the key entirely (LCC8 review F5)."""
    event: dict[str, Any] = {
        "type": "system",
        "subtype": "init",
        "permissionMode": permission_mode,
    }
    if tools is _MISSING_TOOLS:
        pass
    elif tools is None:
        event["tools"] = None
    else:
        event["tools"] = list(tools)
    return json.dumps(event)


def _assistant_line() -> str:
    return json.dumps({"type": "assistant", "message": {"content": []}})


def _result_line(result: str = "ok") -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": result,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def _make_llm_with_stream(
    capabilities: Capabilities, lines: list[str]
) -> tuple[ClaudeCliLLM, _FakeStreamProcess, dict[str, Any]]:
    fake_proc = _FakeStreamProcess(lines)
    captured: dict[str, Any] = {}

    def _stream_runner(
        argv: list[str], *, input_text: str | None, timeout: float
    ) -> _FakeStreamProcess:
        captured["argv"] = argv
        captured["input_text"] = input_text
        captured["timeout"] = timeout
        return fake_proc

    llm = ClaudeCliLLM(
        capabilities=capabilities,
        stream_runner=_stream_runner,  # type: ignore[arg-type]
        runner=lambda *a, **k: (_ for _ in ()).throw(  # noqa: E731
            AssertionError("the non-streaming runner must not be used in this mode")
        ),
    )
    return llm, fake_proc, captured


def _call(llm: ClaudeCliLLM) -> Any:
    return llm.completion(
        model="claude-cli/claude-haiku-4-5-20251001",
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )


# ---------------------------------------------------------------------------
# CLAUDE_IN_CHROME_TOOLS / McpServer
# ---------------------------------------------------------------------------


def test_claude_in_chrome_tools_has_exactly_22_names() -> None:
    assert len(CLAUDE_IN_CHROME_TOOLS) == 22
    assert len(set(CLAUDE_IN_CHROME_TOOLS)) == 22


def test_claude_in_chrome_tools_are_prefixed_and_measured_names() -> None:
    expected = {
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
    }
    assert set(CLAUDE_IN_CHROME_TOOLS) == expected


def test_mcp_server_is_frozen() -> None:
    import dataclasses

    server = McpServer(name="approver", command="python3", args=("-m", "x"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        server.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Capabilities construction: browser+exclusive supported; the five refusals
# ---------------------------------------------------------------------------


def test_browser_exclusive_accepts_any_claude_in_chrome_tool() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    assert cap.tools == FIXTURE_TOOLS


def test_browser_exclusive_rejects_a_tool_outside_the_chrome_inventory() -> None:
    """A `_DISABLED_TOOLS` name (valid in every OTHER mode) is invalid here —
    the inventories are disjoint by construction (no shared names)."""
    with pytest.raises(ValueError, match="Bash"):
        Capabilities(exclusive=True, browser=True, tools=("Bash",))


def test_browser_exclusive_rejects_an_unknown_chrome_tool_name() -> None:
    with pytest.raises(ValueError, match="mcp__claude-in-chrome__nonesuch"):
        Capabilities(
            exclusive=True, browser=True, tools=("mcp__claude-in-chrome__nonesuch",)
        )


def test_mcp_servers_without_browser_exclusive_raises() -> None:
    server = McpServer(name="approver", command="python3", args=())
    with pytest.raises(ValueError, match="exclusive=True, browser=True"):
        Capabilities(mcp_servers=(server,))
    with pytest.raises(ValueError, match="exclusive=True, browser=True"):
        Capabilities(exclusive=True, mcp_servers=(server,))  # browser=False
    with pytest.raises(ValueError, match="exclusive=True, browser=True"):
        Capabilities(browser=True, mcp_servers=(server,))  # exclusive=False


def test_permission_prompt_tool_without_browser_exclusive_raises() -> None:
    with pytest.raises(ValueError, match="exclusive=True, browser=True"):
        Capabilities(permission_prompt_tool="mcp__approver__approve")


def test_permission_prompt_tool_naming_an_absent_server_raises() -> None:
    server = McpServer(name="approver", command="python3", args=())
    with pytest.raises(ValueError, match="mcp__other__approve"):
        Capabilities(
            exclusive=True,
            browser=True,
            mcp_servers=(server,),
            permission_prompt_tool="mcp__other__approve",
        )


def test_permission_prompt_tool_matching_its_server_is_accepted() -> None:
    server = McpServer(name="approver", command="python3", args=("-m", "x"))
    cap = Capabilities(
        exclusive=True,
        browser=True,
        mcp_servers=(server,),
        permission_prompt_tool="mcp__approver__approve",
    )
    assert cap.permission_prompt_tool == "mcp__approver__approve"


def test_duplicate_server_names_raises() -> None:
    a = McpServer(name="dup", command="python3", args=())
    b = McpServer(name="dup", command="python3", args=("--other",))
    with pytest.raises(ValueError, match="duplicate"):
        Capabilities(exclusive=True, browser=True, mcp_servers=(a, b))


@pytest.mark.parametrize(
    "bad_name", ["Approver", "1approver", "approver-x", "approver!", "", "APPROVER"]
)
def test_server_name_outside_the_pattern_raises(bad_name: str) -> None:
    server = McpServer(name=bad_name, command="python3", args=())
    with pytest.raises(ValueError, match=r"\^\[a-z\]\[a-z0-9_\]\*\$"):
        Capabilities(exclusive=True, browser=True, mcp_servers=(server,))


def test_valid_server_names_are_accepted() -> None:
    for name in ("approver", "a", "a1", "a_b_c", "jsp_approver"):
        server = McpServer(name=name, command="python3", args=())
        Capabilities(exclusive=True, browser=True, mcp_servers=(server,))


# ---------------------------------------------------------------------------
# argv: browser+exclusive, in the mandated order
# ---------------------------------------------------------------------------


def _make_llm(capabilities: Capabilities) -> tuple[ClaudeCliLLM, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def _stream_runner(
        argv: list[str], *, input_text: str | None, timeout: float
    ) -> _FakeStreamProcess:
        captured["argv"] = argv
        return _FakeStreamProcess([_init_line(capabilities.tools), _result_line()])

    llm = ClaudeCliLLM(capabilities=capabilities, stream_runner=_stream_runner)  # type: ignore[arg-type]
    return llm, captured


def test_browser_exclusive_output_format_is_stream_json_verbose_not_json() -> None:
    llm, captured = _make_llm(Capabilities(exclusive=True, browser=True))
    _call(llm)
    argv = captured["argv"]
    idx = argv.index("--output-format")
    assert argv[idx : idx + 3] == ["--output-format", "stream-json", "--verbose"]
    assert "json" not in argv[idx : idx + 2]


def test_browser_exclusive_block_order_without_mcp_servers() -> None:
    """LCC8 review F8: `--permission-mode default` is unconditional in this
    mode, even with no prompt tool — so a settings file's own default can
    never take over a call that has no approver."""
    cap = Capabilities(
        exclusive=True, browser=True, tools=("mcp__claude-in-chrome__navigate",)
    )
    llm, captured = _make_llm(cap)
    _call(llm)
    argv = captured["argv"]
    block = argv[argv.index("--model") + 2 :]  # skip model value
    disallowed = [t for t in CLAUDE_IN_CHROME_TOOLS if t != cap.tools[0]]
    expected: list[str] = [
        "--chrome",
        "--strict-mcp-config",
        "--tools",
        "",
        "--permission-mode",
        "default",
    ]
    for t in disallowed:
        expected += ["--disallowed-tools", t]
    assert block == expected


def test_browser_exclusive_never_emits_bypass_permissions() -> None:
    server = McpServer(name="approver", command="python3", args=("-m", "x"))
    cap = Capabilities(
        exclusive=True,
        browser=True,
        mcp_servers=(server,),
        permission_prompt_tool="mcp__approver__approve",
    )
    llm, captured = _make_llm(cap)
    _call(llm)
    assert "bypassPermissions" not in captured["argv"]


def test_browser_exclusive_full_block_with_mcp_server_and_prompt_tool() -> None:
    server = McpServer(
        name="jsp_approver",
        command="python3",
        args=("-m", "jsp.linkedin_capture_approver"),
    )
    granted = (
        "mcp__claude-in-chrome__navigate",
        "mcp__claude-in-chrome__select_browser",
    )
    cap = Capabilities(
        exclusive=True,
        browser=True,
        tools=granted,
        mcp_servers=(server,),
        permission_prompt_tool="mcp__jsp_approver__approve",
    )
    llm, captured = _make_llm(cap)
    _call(llm)
    argv = captured["argv"]
    block = argv[argv.index("--model") + 2 :]

    expected_mcp_config = json.dumps(
        {
            "mcpServers": {
                "jsp_approver": {
                    "command": "python3",
                    "args": ["-m", "jsp.linkedin_capture_approver"],
                }
            }
        },
        separators=(",", ":"),
    )
    disallowed = [t for t in CLAUDE_IN_CHROME_TOOLS if t not in granted]
    expected: list[str] = [
        "--chrome",
        "--mcp-config",
        expected_mcp_config,
        "--strict-mcp-config",
        "--tools",
        "",
        "--permission-mode",
        "default",
        "--permission-prompt-tool",
        "mcp__jsp_approver__approve",
    ]
    for t in disallowed:
        expected += ["--disallowed-tools", t]
    assert block == expected


def test_mcp_config_dict_order_follows_tuple_order() -> None:
    s1 = McpServer(name="alpha", command="cmd1", args=())
    s2 = McpServer(name="beta", command="cmd2", args=())
    cap = Capabilities(exclusive=True, browser=True, mcp_servers=(s1, s2))
    llm, captured = _make_llm(cap)
    _call(llm)
    argv = captured["argv"]
    encoded = argv[argv.index("--mcp-config") + 1]
    assert encoded == json.dumps(
        {
            "mcpServers": {
                "alpha": {"command": "cmd1", "args": []},
                "beta": {"command": "cmd2", "args": []},
            }
        },
        separators=(",", ":"),
    )
    # Reversed tuple order must reverse dict order too (Python dicts preserve
    # insertion order, and json.dumps does not reorder keys).
    cap2 = Capabilities(exclusive=True, browser=True, mcp_servers=(s2, s1))
    llm2, captured2 = _make_llm(cap2)
    _call(llm2)
    encoded2 = captured2["argv"][captured2["argv"].index("--mcp-config") + 1]
    assert list(json.loads(encoded2)["mcpServers"].keys()) == ["beta", "alpha"]


def test_no_mcp_config_flag_without_mcp_servers() -> None:
    llm, captured = _make_llm(Capabilities(exclusive=True, browser=True))
    _call(llm)
    assert "--mcp-config" not in captured["argv"]


def test_permission_mode_default_always_present_but_prompt_tool_flag_is_not() -> None:
    """LCC8 review F8: `--permission-mode default` is unconditional; only
    `--permission-prompt-tool` is gated on `permission_prompt_tool`."""
    llm, captured = _make_llm(Capabilities(exclusive=True, browser=True))
    _call(llm)
    argv = captured["argv"]
    idx = argv.index("--permission-mode")
    assert argv[idx + 1] == "default"
    assert "--permission-prompt-tool" not in argv


# ---------------------------------------------------------------------------
# The fail-closed `system/init` stream checks
# ---------------------------------------------------------------------------


def test_recorded_stream_builds_the_same_response_shape_as_json_mode() -> None:
    """The redacted fixture, driven through the real streaming path end to
    end, parses into a ModelResponse exactly as `_build_response` does for
    `json` mode — proving the result event's shape is reused correctly."""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    llm, fake_proc, _ = _make_llm_with_stream(cap, _fixture_lines())
    resp = _call(llm)
    assert "Step 1 (select_browser)" in resp.choices[0].message.content
    assert resp.choices[0].finish_reason == "stop"
    assert resp.usage.prompt_tokens == 50
    assert resp.usage.completion_tokens == 994
    assert resp.usage.cache_read_input_tokens == 92569
    assert resp.usage.cache_creation_input_tokens == 8834
    assert not fake_proc.killed


def test_stream_tool_set_mismatch_kills_and_raises() -> None:
    cap = Capabilities(
        exclusive=True, browser=True, tools=("mcp__claude-in-chrome__navigate",)
    )
    lines = [_init_line(FIXTURE_TOOLS), _result_line()]  # offers 6, grant is 1
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="tools"):
        _call(llm)
    assert fake_proc.killed


def test_stream_permission_mode_mismatch_kills_and_raises() -> None:
    server = McpServer(name="approver", command="python3", args=())
    cap = Capabilities(
        exclusive=True,
        browser=True,
        tools=FIXTURE_TOOLS,
        mcp_servers=(server,),
        permission_prompt_tool="mcp__approver__approve",
    )
    lines = [_init_line(FIXTURE_TOOLS, permission_mode="acceptEdits"), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="permissionMode"):
        _call(llm)
    assert fake_proc.killed


def test_stream_permission_mode_checked_even_without_a_prompt_tool() -> None:
    """LCC8 review F8: the permissionMode check is unconditional now — argv
    always emits `--permission-mode default` in this mode, so a call with no
    approver must still fail closed on `acceptEdits`/`bypassPermissions`/etc,
    never inherit a settings file's own default."""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [_init_line(FIXTURE_TOOLS, permission_mode="acceptEdits"), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="permissionMode"):
        _call(llm)
    assert fake_proc.killed


def test_stream_permission_mode_bypass_fails_closed_without_a_prompt_tool() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [
        _init_line(FIXTURE_TOOLS, permission_mode="bypassPermissions"),
        _result_line(),
    ]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="permissionMode"):
        _call(llm)
    assert fake_proc.killed


def test_stream_assistant_before_init_kills_and_raises() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [_assistant_line(), _init_line(FIXTURE_TOOLS), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="assistant"):
        _call(llm)
    assert fake_proc.killed


def test_stream_result_before_init_kills_and_raises() -> None:
    """A malformed stream with a `result` and no `init` at all must not
    silently succeed — the init check is the outer fence, not a check that
    can be skipped by a stream that never reaches it."""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [_result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="init"):
        _call(llm)
    assert fake_proc.killed


def test_stream_ends_without_init_kills_and_raises() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines: list[str] = []
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="system/init"):
        _call(llm)
    assert fake_proc.killed


def test_stream_ends_without_result_kills_and_raises() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [_init_line(FIXTURE_TOOLS)]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="result"):
        _call(llm)
    assert fake_proc.killed


def test_stream_ignores_blank_lines() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = ["", _init_line(FIXTURE_TOOLS), "  ", _result_line("done"), ""]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    resp = _call(llm)
    assert resp.choices[0].message.content == "done"
    assert not fake_proc.killed


def test_stream_runner_receives_argv_input_text_and_timeout() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [_init_line(FIXTURE_TOOLS), _result_line()]
    llm, _, captured = _make_llm_with_stream(cap, lines)
    llm._timeout = 42.0  # type: ignore[attr-defined]
    _call(llm)
    assert captured["input_text"] == "go"
    assert captured["timeout"] == 42.0
    assert "--chrome" in captured["argv"]


# ---------------------------------------------------------------------------
# F5: a missing or null `tools` key must fail closed, `tools=()` included
# ---------------------------------------------------------------------------


def test_stream_init_missing_tools_key_fails_closed_even_with_empty_grant() -> None:
    """Before the fix, `set(event.get("tools") or [])` turned a MISSING key
    into `set()`, which equalled `set(())` — an init proving nothing about
    the offered tools passed when the grant was empty. Must now fail."""
    cap = Capabilities(exclusive=True, browser=True, tools=())
    lines = [_init_line(_MISSING_TOOLS), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="tools"):
        _call(llm)
    assert fake_proc.killed


def test_stream_init_null_tools_fails_closed_even_with_empty_grant() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=())
    lines = [_init_line(None), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="tools"):
        _call(llm)
    assert fake_proc.killed


def test_stream_init_missing_tools_key_fails_closed_with_a_real_grant() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [_init_line(_MISSING_TOOLS), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="tools"):
        _call(llm)
    assert fake_proc.killed


# ---------------------------------------------------------------------------
# F9: init offering fewer tools than granted; a second init event
# ---------------------------------------------------------------------------


def test_stream_init_offers_fewer_tools_than_granted_fails_closed() -> None:
    """Equality, not subset: an init offering a STRICT SUBSET of the grant
    must fail exactly like one offering extras. (LCC8 review mutant A: a
    `got_tools - want_tools` comparison tolerates this and survives.)"""
    granted = (
        "mcp__claude-in-chrome__navigate",
        "mcp__claude-in-chrome__select_browser",
    )
    cap = Capabilities(exclusive=True, browser=True, tools=granted)
    lines = [_init_line(("mcp__claude-in-chrome__navigate",)), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="tools"):
        _call(llm)
    assert fake_proc.killed


def test_stream_second_init_with_wrong_tools_fails_closed() -> None:
    """Every `init` event is checked, not only the first. (LCC8 review mutant
    B: gating the check on `not init_seen` tolerates a bad second init and
    survives.)"""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = [
        _init_line(FIXTURE_TOOLS),
        _init_line(("mcp__claude-in-chrome__navigate",)),  # a bad second init
        _result_line(),
    ]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="tools"):
        _call(llm)
    assert fake_proc.killed


# ---------------------------------------------------------------------------
# F1: ANY exception out of the loop kills the process before propagating
# ---------------------------------------------------------------------------


def test_stream_non_object_json_line_kills_and_raises() -> None:
    """Valid JSON that isn't an object (e.g. `[1,2]`) used to reach
    `event.get(...)` and raise an uncaught `AttributeError` with no kill."""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = ["[1,2]", _init_line(FIXTURE_TOOLS), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="JSON object"):
        _call(llm)
    assert fake_proc.killed


def test_stream_malformed_json_line_kills_and_raises_runtime_error() -> None:
    """A non-JSON line is classified as RuntimeError, not the JSONDecodeError
    it would otherwise surface as (LCC8 review N2): this module reserves
    ValueError for caller error, and json mode raises RuntimeError for the
    same non-JSON-output situation. The kill still happens either way."""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    lines = ["not json at all", _init_line(FIXTURE_TOOLS), _result_line()]
    llm, fake_proc, _ = _make_llm_with_stream(cap, lines)
    with pytest.raises(RuntimeError, match="non-JSON"):
        _call(llm)
    assert fake_proc.killed


# ---------------------------------------------------------------------------
# F6: timeout raises TimeoutExpired; a non-zero exit is exhaustion-classified
# ---------------------------------------------------------------------------


def test_stream_watchdog_timeout_raises_timeout_expired() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    fake_proc = _FakeStreamProcess([_init_line(FIXTURE_TOOLS)])  # no result; EOF
    fake_proc.mark_timed_out()

    def _stream_runner(
        argv: list[str], *, input_text: str | None, timeout: float
    ) -> _FakeStreamProcess:
        return fake_proc

    llm = ClaudeCliLLM(capabilities=cap, stream_runner=_stream_runner)  # type: ignore[arg-type]
    with pytest.raises(subprocess.TimeoutExpired):
        _call(llm)
    assert fake_proc.killed


def test_stream_nonzero_exit_with_exhaustion_marker_raises_claude_exhausted() -> None:
    """A watchdog-free EOF (no result, no init even) with a non-zero exit and
    an exhaustion marker on stderr must classify the same way `_default_runner`
    classifies combined stdout+stderr for the `json`-mode path."""
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    fake_proc = _FakeStreamProcess(
        [],
        finish_returncode=1,
        finish_stderr="You've hit your usage limit. resets 3pm PST.",
    )

    def _stream_runner(
        argv: list[str], *, input_text: str | None, timeout: float
    ) -> _FakeStreamProcess:
        return fake_proc

    llm = ClaudeCliLLM(capabilities=cap, stream_runner=_stream_runner)  # type: ignore[arg-type]
    with pytest.raises(ClaudeExhausted) as excinfo:
        _call(llm)
    assert "3pm" in (excinfo.value.reset_hint or "")
    assert fake_proc.killed


def test_stream_nonzero_exit_without_exhaustion_marker_raises_runtime_error() -> None:
    cap = Capabilities(exclusive=True, browser=True, tools=FIXTURE_TOOLS)
    fake_proc = _FakeStreamProcess([], finish_returncode=1, finish_stderr="boom")

    def _stream_runner(
        argv: list[str], *, input_text: str | None, timeout: float
    ) -> _FakeStreamProcess:
        return fake_proc

    llm = ClaudeCliLLM(capabilities=cap, stream_runner=_stream_runner)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="failed \\(1\\)"):
        _call(llm)
    assert fake_proc.killed
