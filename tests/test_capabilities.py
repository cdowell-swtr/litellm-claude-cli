"""`Capabilities` construction, validation, and the argv it produces."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from litellm_claude_cli import (
    _DISABLED_TOOLS,
    _disabled_tools_for,
    Capabilities,
    ClaudeCliLLM,
)


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


def _fake_json_response() -> str:
    """Return a minimal valid JSON response that the CLI would return."""
    return json.dumps(
        {
            "result": "test response",
            "stop_reason": "stop",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
            },
        }
    )


def _make_llm(
    capabilities: Capabilities | None,
) -> tuple[ClaudeCliLLM, dict[str, Any]]:
    """Create a ClaudeCliLLM with a captured runner that records argv."""
    captured: dict[str, Any] = {}

    def _fake_runner(
        argv: list[str], *, input_text: str | None, timeout: float = 600.0
    ) -> str:
        captured["argv"] = argv
        return _fake_json_response()

    return ClaudeCliLLM(runner=_fake_runner, capabilities=capabilities), captured


def _disallowed(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--disallowed-tools"]


def _call(
    llm: ClaudeCliLLM, model: str = "claude-cli/claude-haiku-4-5-20251001"
) -> None:
    llm.completion(
        model=model,
        messages=[{"role": "user", "content": "go"}],
        optional_params={},
    )


def test_capabilities_none_leaves_the_real_argv_untouched() -> None:
    """THE load-bearing default.  A hardcoded whole-argv literal, not a captured
    baseline: a baseline would compare one code path to itself and could only
    pass.  Any change to default argv must be deliberate and update this test in
    the same commit.

    The known consumer pins its own copy of this argv shape against a released
    version, so a change here is a cross-boundary change: it needs a version bump
    on this side before that side can adopt it."""
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
        "--disable-slash-commands",
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


def test_slash_commands_are_disabled_regardless_of_capabilities() -> None:
    """`--disable-slash-commands` is fixed argv, not a capability.

    It is emitted exactly once on every call — with no capabilities, with tools
    granted, and with the browser attached.  Two things rest on it: skills are
    unusable on a one-shot `-p` call, and the `Skill` tool sits OUTSIDE
    `_DISABLED_TOOLS`, so this flag is what stops a call taking a second turn
    through a skill.  A capability that granted skills back would break the
    one-model-turn invariant `_DISABLED_TOOLS` exists to hold, which is why
    there is no such capability.
    """
    for capabilities in (
        None,
        Capabilities(),
        Capabilities(tools=("Read", "Grep")),
        Capabilities(browser=True),
        Capabilities(tools=_DISABLED_TOOLS, browser=True),
    ):
        llm, captured = _make_llm(capabilities)
        _call(llm)
        argv = captured["argv"]
        assert argv.count("--disable-slash-commands") == 1, (
            f"--disable-slash-commands emitted {argv.count('--disable-slash-commands')} "
            f"time(s) for capabilities={capabilities!r}; it must be emitted exactly "
            "once on every call"
        )


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
    model may drive a browser while the ten listed tools stay disabled."""
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
