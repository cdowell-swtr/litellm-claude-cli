"""LCC8 review F3/F7: the real streaming runner and kill actuator.

Every test in `test_browser_exclusive.py` drives `_read_stream_result` with a
`_FakeStreamProcess` double, which proves `kill()` was CALLED but never that
the underlying OS process actually STOPPED. These tests point the REAL
`_default_stream_runner` (`subprocess.Popen` + the watchdog timer) at a small
fake CLI script and assert the process — and any child it spawned — is
genuinely dead afterward, closing that gap.

No real `claude` binary or Chrome is involved anywhere here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

import pytest

from litellm_claude_cli import (
    Capabilities,
    ClaudeExhausted,
    _default_stream_runner,
    _read_stream_result,
)

# A tiny stand-in CLI, invoked as `<python> fake_cli.py <scenario> [pidfile]`.
# It never reads stdin, so callers pass input_text=None.
_FAKE_CLI_SOURCE = textwrap.dedent(
    """
    import json
    import subprocess
    import sys
    import time

    def main() -> None:
        scenario = sys.argv[1]
        pidfile = sys.argv[2] if len(sys.argv) > 2 else None

        if pidfile:
            # A grandchild in the same process group (no start_new_session),
            # so a killpg of the fake CLI's group should take it too.
            child = subprocess.Popen(["sleep", "60"])
            with open(pidfile, "w") as fh:
                fh.write(str(child.pid))

        if scenario == "bad_init":
            print(json.dumps({
                "type": "system", "subtype": "init",
                "tools": ["mcp__claude-in-chrome__navigate"],
                "permissionMode": "default",
            }), flush=True)
            time.sleep(60)
        elif scenario == "malformed":
            print("not-json-at-all", flush=True)
            time.sleep(60)
        elif scenario == "hang":
            time.sleep(60)
        elif scenario == "clean":
            print(json.dumps({
                "type": "system", "subtype": "init",
                "tools": ["mcp__claude-in-chrome__navigate"],
                "permissionMode": "default",
            }), flush=True)
            print(json.dumps({
                "type": "result", "is_error": False, "result": "ok",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }), flush=True)
            sys.exit(0)
        elif scenario == "exhausted":
            sys.stderr.write("Error: usage limit reached. resets 3pm PST.\\n")
            sys.stderr.flush()
            sys.exit(1)
        sys.exit(0)

    if __name__ == "__main__":
        main()
    """
)


@pytest.fixture()
def fake_cli(tmp_path):
    path = tmp_path / "fake_cli.py"
    path.write_text(_FAKE_CLI_SOURCE)
    return path


def _argv(fake_cli_path, scenario: str, pidfile=None) -> list[str]:
    argv = [sys.executable, str(fake_cli_path), scenario]
    if pidfile is not None:
        argv.append(str(pidfile))
    return argv


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover — not expected for our own children
        return True
    return True


def _wait_until_dead(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.02)
    return not _is_alive(pid)


def _pid_of(proc) -> int:
    return proc._proc.pid  # reaching into the concrete class under test


# ---------------------------------------------------------------------------
# F3: the kill actuator actually stops the process
# ---------------------------------------------------------------------------


def test_real_runner_kills_the_process_on_a_failed_init_check(fake_cli) -> None:
    proc = _default_stream_runner(
        _argv(fake_cli, "bad_init"), input_text=None, timeout=30.0
    )
    cap = Capabilities(
        exclusive=True, browser=True, tools=("mcp__claude-in-chrome__select_browser",)
    )
    with pytest.raises(RuntimeError, match="tools"):
        _read_stream_result(proc, cap, 30.0)
    assert _wait_until_dead(_pid_of(proc))


def test_real_runner_kills_the_process_on_a_malformed_line(fake_cli) -> None:
    """Also the F1 regression test: a malformed line must still result in the
    real process being killed, not just an exception being raised."""
    proc = _default_stream_runner(
        _argv(fake_cli, "malformed"), input_text=None, timeout=30.0
    )
    cap = Capabilities(exclusive=True, browser=True)
    with pytest.raises(RuntimeError, match="non-JSON"):
        _read_stream_result(proc, cap, 30.0)
    assert _wait_until_dead(_pid_of(proc))


# ---------------------------------------------------------------------------
# F6: the watchdog fires a real TimeoutExpired, well inside the fake CLI's
# own 60s sleep, and still kills the process
# ---------------------------------------------------------------------------


def test_real_runner_watchdog_kills_on_timeout(fake_cli) -> None:
    proc = _default_stream_runner(_argv(fake_cli, "hang"), input_text=None, timeout=0.3)
    cap = Capabilities(exclusive=True, browser=True)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _read_stream_result(proc, cap, 0.3)
    elapsed = time.monotonic() - start
    assert elapsed < 10.0, f"took {elapsed}s — the 60s sleep leaked past the watchdog"
    assert _wait_until_dead(_pid_of(proc))


def test_real_runner_exhaustion_from_stderr_after_nonzero_exit(fake_cli) -> None:
    proc = _default_stream_runner(
        _argv(fake_cli, "exhausted"), input_text=None, timeout=30.0
    )
    cap = Capabilities(exclusive=True, browser=True)
    with pytest.raises(ClaudeExhausted) as excinfo:
        _read_stream_result(proc, cap, 30.0)
    assert "3pm" in (excinfo.value.reset_hint or "")
    assert _wait_until_dead(_pid_of(proc))


# ---------------------------------------------------------------------------
# F7: killpg reaches a grandchild; the success path reaps rather than leaking
# ---------------------------------------------------------------------------


def test_real_runner_kills_the_grandchild_too(fake_cli, tmp_path) -> None:
    pidfile = tmp_path / "grandchild.pid"
    proc = _default_stream_runner(
        _argv(fake_cli, "malformed", pidfile), input_text=None, timeout=30.0
    )
    cap = Capabilities(exclusive=True, browser=True)
    with pytest.raises(RuntimeError, match="non-JSON"):
        _read_stream_result(proc, cap, 30.0)

    deadline = time.monotonic() + 3.0
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert pidfile.exists(), "the fake CLI never wrote its grandchild's pid"
    grandchild_pid = int(pidfile.read_text())
    assert _wait_until_dead(grandchild_pid), "the grandchild survived the kill"


def test_real_runner_success_path_leaves_no_live_process(fake_cli) -> None:
    proc = _default_stream_runner(
        _argv(fake_cli, "clean"), input_text=None, timeout=30.0
    )
    cap = Capabilities(
        exclusive=True, browser=True, tools=("mcp__claude-in-chrome__navigate",)
    )
    raw = _read_stream_result(proc, cap, 30.0)
    assert json.loads(raw)["result"] == "ok"
    assert _wait_until_dead(_pid_of(proc))


# ---------------------------------------------------------------------------
# N1: the captured-stderr temp file must not leak on ANY exit path. Each test
# injects its own directory (`stderr_dir`) rather than sharing the platform
# temp dir, so the snapshot is exact regardless of what else is running.
# ---------------------------------------------------------------------------


def test_real_runner_success_path_leaves_no_stderr_tempfile(fake_cli, tmp_path) -> None:
    stderr_dir = tmp_path / "stderr"
    stderr_dir.mkdir()
    proc = _default_stream_runner(
        _argv(fake_cli, "clean"),
        input_text=None,
        timeout=30.0,
        stderr_dir=str(stderr_dir),
    )
    cap = Capabilities(
        exclusive=True, browser=True, tools=("mcp__claude-in-chrome__navigate",)
    )
    _read_stream_result(proc, cap, 30.0)
    assert list(stderr_dir.glob("*.stderr")) == []


def test_real_runner_failed_init_check_leaves_no_stderr_tempfile(
    fake_cli, tmp_path
) -> None:
    stderr_dir = tmp_path / "stderr"
    stderr_dir.mkdir()
    proc = _default_stream_runner(
        _argv(fake_cli, "bad_init"),
        input_text=None,
        timeout=30.0,
        stderr_dir=str(stderr_dir),
    )
    cap = Capabilities(
        exclusive=True, browser=True, tools=("mcp__claude-in-chrome__select_browser",)
    )
    with pytest.raises(RuntimeError, match="tools"):
        _read_stream_result(proc, cap, 30.0)
    assert list(stderr_dir.glob("*.stderr")) == []


def test_real_runner_malformed_line_leaves_no_stderr_tempfile(
    fake_cli, tmp_path
) -> None:
    stderr_dir = tmp_path / "stderr"
    stderr_dir.mkdir()
    proc = _default_stream_runner(
        _argv(fake_cli, "malformed"),
        input_text=None,
        timeout=30.0,
        stderr_dir=str(stderr_dir),
    )
    cap = Capabilities(exclusive=True, browser=True)
    with pytest.raises(RuntimeError, match="non-JSON"):
        _read_stream_result(proc, cap, 30.0)
    assert list(stderr_dir.glob("*.stderr")) == []


# ---------------------------------------------------------------------------
# Mutation-proving F3 (per the LCC8 review, mutants C and D)
# ---------------------------------------------------------------------------
#
# These two are proven by hand against the source, the same way as the
# stream-check mutants: apply the mutation, run the test below alone, observe
# the failure, revert, byte-compare. See ACTION_LOG.md #0017 for the table
# (mutant, test, observed output) — nothing here runs the mutation itself,
# since the source is not something a test can toggle at runtime.
