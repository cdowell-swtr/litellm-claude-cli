"""`Capabilities` construction, validation, and the argv it produces."""

from __future__ import annotations

import dataclasses

import pytest
from litellm_claude_cli import _DISABLED_TOOLS, Capabilities


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
