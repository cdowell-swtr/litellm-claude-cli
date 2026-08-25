"""`Capabilities` construction, validation, and the argv it produces."""

from __future__ import annotations

import dataclasses

import pytest
from litellm_claude_cli import (
    _DISABLED_TOOLS,
    _disabled_tools_for,
    Capabilities,
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
