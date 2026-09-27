# ACTION_LOG

Append-only event narrative: completions, deviations, and operational reasons at task grain.
Never edit or truncate existing entries. Event taxonomy: `completed · inserted · reordered ·
dep-found · amended · superseded · discarded · milestone · note`. Per `pi-convention.md`.

#### #0001 · note · 2026-06-14
Adopted the Planning Instrument convention (PI-convention: v2), repo prefix `LCC`.

#### #0002 · completed · LCC1 · 2026-06-14
Adopted four patterns conventions from cdowell-swtr/patterns: PI (v2), Committed Memory (v1),
Git (v1), Docs-layout (v1). Vendored convention docs at root; scaffolded PI + MEMORY stores;
wired AGENTS.md/CLAUDE.md pointers; wired pre-commit (gitleaks + conventional-pre-commit +
docs-layout) and CI backstops. Operational reason: bring this consumer repo onto the shared
engineering conventions.

#### #0003 · note · 2026-06-14
CI fix after PR #1's first run failed: the `conventions` job ran `pre-commit run docs-layout`,
which clones the private patterns repo — Actions' GITHUB_TOKEN can't, so it errored. Switched CI
to the vendored `hooks/docs-layout-check.sh` and gitleaks to direct-binary install (matching the
patterns reference workflow). Local pre-commit hooks unchanged. Gotcha recorded in committed
memory ([[ci-docs-layout-vendored-script]]).

#### #0004 · completed · LCC2 · 2026-06-14
Registration PR (patterns#4) merged — litellm-claude-cli now recorded in all four implementer
registries: PI (v2, prefix LCC), Committed Memory (v1), Git (v1), Docs-layout (v1).

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

#### #0007 · amended · LCC3 · 2026-08-11
Whole-branch review found the release notes asserted an error-taxonomy guarantee the recommended
call path does not deliver: `litellm.completion()` wraps every provider exception in
`APIConnectionError`, so `ClaudeExhausted` / `RuntimeError` / `ValueError` are recoverable only via
`exc.__context__` (`__cause__` is `None`). Pre-existing runtime behaviour, newly mis-described —
CHANGELOG and README corrected and the wrapping pinned by test. Also: `>` → `>=` at the argv
ceiling (Linux counts the NUL terminator, so exactly 131072 bytes was the first *rejected* length,
not the last accepted one), plus tests pinning the `pre_made_response` copy path and
`_DISABLED_TOOLS` exhaustiveness. Operational reason: the `v0.2.0` tag was retagged onto the
amended head, so the release a consumer installs contains these corrections.

#### #0008 · inserted · LCC4 · 2026-08-24
First-class capabilities for the jsp consumer, so it can delete the in-repo argv wrapper that
reaches across the dependency boundary into `self._runner`. Design spec at
`_docs/provider/superpowers/specs/2026-08-24-capabilities-design.md`. Scope agreed with the
consumer's orchestrator against its brief; two of its statements were re-derived rather than
adopted. Its `--chrome`-at-end requirement was an artefact of appending to a finished argv (the
CLI does not care), and its "surface raw `stop_reason` unchanged" fallback would have handed
LiteLLM a `finish_reason` this provider cannot honour, since it never emits a `tool_calls`
array. The brief's `_DISABLED_TOOLS` count (11) was wrong against source (10) and was corrected
upstream. Operational reason: the consumer's wrapper survives only while its pin is frozen, so
any provider release meets it as a silent conflict.

#### #0009 · completed · LCC4 · 2026-08-24
Shipped 0.3.0: `Capabilities(tools, browser)` as an optional `ClaudeCliLLM` parameter,
argv built from it rather than rewritten, validation on the dataclass (unknown or
wrong-case tool name raises `ValueError`), and `tool_use` → `stop` re-keyed
unconditionally rather than only on the structured-output path.

Two existing tests changed rather than broke. `test_finish_reason_untouched_without_schema`
pinned the retired premise that only the structured path produced `tool_use`; it was
replaced by `test_finish_reason_never_emits_tool_calls`, which asserts the mapping
holds regardless of cause. The `_DISABLED_TOOLS` tripwire test kept every assertion and
hardcoded name unchanged — only its stated rationale changed, since it had justified
itself by referencing the premise that was just retired.

Live-test finding: the first live run failed, and the cause was not the design. A
granted tool only reaches files under the CLI's working directory; the test had
targeted pytest's `tmp_path`, which sits outside it. A controlled A/B with
byte-identical flags confirmed the grant mechanism itself works: a file inside the cwd
was read by the tool; the identical call against a file outside the cwd got "I need
permission to read the file." Fixed by `monkeypatch.chdir(tmp_path)`; the test's
assertions were unchanged.

Evidence obtained: with the Read tool actually executing and a schema requested in the
same real call, the CLI's raw `stop_reason` is `tool_use`, mapping to `finish_reason:
"stop"`, with `structured_output` present. This value had never been observed for this
configuration before, and it confirms the re-key against the real CLI rather than only
against mocks.

Failure signature worth knowing for future debugging: a tool-granted call whose target
is outside the cwd returns `finish_reason: "stop"`, no `structured_output`, and a prose
refusal in the message content — indistinguishable from ordinary invalid model output
except by the raw `stop_reason`, which is `end_turn` for the outside-cwd wall versus
`tool_use` for a genuinely truncated turn.

#### #0010 · note · 2026-08-24
Docs accuracy sweep after v0.3.0: four sites still said `None`/omitting `capabilities`
disables "every tool", when only the ten in `_DISABLED_TOOLS` are ever disabled — tools
outside that list (TodoWrite, BashOutput, Skill, MCP tools) were never disabled and remain
available. The v0.3.0 final review caught this class and the fix wave corrected the README
and module docstring, but its scope missed `ClaudeCliLLM.__init__`'s own `capabilities:`
docstring, two test docstrings, and a CHANGELOG line, so the overclaim shipped in v0.3.0.
Corrected here. Deliberately left: the two places that QUOTE 0.2.0's retired premise as
history (`_build_response`'s SOUNDNESS comment and the inverted finish_reason test) — those
are accurate as quotations. Docs only, no behaviour change, no version bump.

#### #0011 · note · LCC5 · 2026-09-02
Recorded after the fact, by a later task, from the commit alone — not by the author. LCC5
(configurable call timeout, `ClaudeCliLLM(timeout=...)`) shipped as v0.3.1 in commit 2051607
on 2026-08-26 with no PLAN item and no log entry; `PLAN.md`'s Done list still ended at LCC4
and this log at #0010. Found while starting the next task, which reused the free-looking ID
LCC5 and the free-looking version 0.3.1 — both already taken. The PLAN line added alongside
this entry states what shipped; the reasoning behind the timeout lives only in that commit
message.

Also found: v0.3.1 and its commit live on `origin/main`, a branch that does NOT contain
`origin/master`'s tip and is not contained by it, while `origin/HEAD` still points at
`master`. A clone that follows the default branch gets a tree without the latest release, and
`git tag` locally showed v0.3.0 as the newest until `--tags` was fetched explicitly. Left as
found — resolving the two branch names is Chris's call, not this task's.

#### #0012 · completed · LCC6 · 2026-09-02
`--disable-slash-commands` added to fixed argv, next to
`--exclude-dynamic-system-prompt-sections`. Requested by the known consumer (jsp) as a
per-call context-cost saving: it reported ~1.9k tokens saved per call and a warm
cache-write floor dropping from 2.7–5k to 1,167, measured on CLI 2.1.235 in minimal
worker containers.

That saving did not reproduce here and reversed. A/B on CLI 2.1.259, identical argv but
for the flag, three warm calls per arm and interleaved to rule out ordering: cached
prefix 9,644 tokens without the flag, 11,877 with it — byte-stable, order-independent,
and the same direction under `--safe-mode` (7,129 vs 9,773 total prefix). A probe call
confirmed the flag does what it says — with it, the 28-skill listing and the `Skill`
tool are gone from context — so the extra ~2.2k is something the CLI adds when skills
are off, mechanism unidentified.

Resolved by the consumer re-running the interleaved method on both CLIs on one machine:
the sign flips with CLI version. 2.1.235 saves 1,927 tokens/call (19,673 → 17,746);
2.1.259 costs 2,195 (24,038 → 26,233), the direction measured here. Neither party was
wrong; the disagreement was one uncontrolled variable. The consumer keeps the saving on
its pinned 2.1.235 image and has put a re-run of this A/B on its image-bump checklist,
since a CLI upgrade can silently invert the rationale.

Shipped anyway, but on a different argument than the one requested, stated in the
CHANGELOG rather than the cost one: `Skill` sits outside `_DISABLED_TOOLS` (noted in
#0010 as never disabled), so a skill was the one remaining route by which a call could
take a second turn. The flag closes it, and the one-model-turn invariant now holds
against the CLI's full tool surface. Unconditional, matching the consumer's preferred
shape; no capability grants skills back, since one would reopen exactly that route.

Cross-boundary consequence: argv is no longer byte-identical to the pre-capabilities
build for `capabilities=None`, retiring a claim 0.3.0 made in three places (the
`_disabled_tools_for` and `__init__` docstrings, the CHANGELOG). The consumer pins this
argv shape against a released version, so this needed a version bump — v0.3.2, rebased
onto `origin/main` and with `uv lock` re-run per [[uv-lock-self-version-drift]]. The
first cut of this work was built on `master` as LCC5/v0.3.1; both were already taken by
the unlogged timeout release (#0011), which the consumer's pin evidence surfaced.

Verification: 65 unit tests pass, and the live smoke suite passes against the real CLI
with the flag in argv — no functional loss on the one-shot path.

#### #0013 · note · 2026-09-03
Guards added after v0.3.2 shipped, closing the hole that let v0.3.1 reach a tag and the
downstream pin without CI ever running on it. Root cause was the workflow trigger, not the
branch topology: `on: push: branches: [master]` matched nothing when the commit landed on a
stray branch (never canonical, since deleted), and with no PR the `pull_request` trigger never
fired either. Widened to
`branches: ['**']`, with a `concurrency` group so a PR branch matching both triggers keeps
only its newest run.

Branch protection on `master` requiring the `ci` check was added alongside, but it is the
weaker of the two: protection defends one branch, and the failure was a push to a branch
that did not exist yet. The trigger is what actually closes it.

Recorded as committed memory [[ci-runs-on-every-branch]], which also carries the
generalisable half — when a version or tag appears not to exist, `git fetch --tags` before
concluding it doesn't. That mistake was caught here only because the consumer pinned a commit
hash that could not be resolved locally; unaided, this repo would have published a second,
conflicting v0.3.1.

#### #0014 · completed · LCC7 · 2026-09-24
v0.4.0, from jsp's brief `2026-09-25-litellm-claude-cli-allowlist-and-model-usage`
(materials lane). Two changes.

`Capabilities(exclusive=True)` emits `--tools <grant> --strict-mcp-config` and no deny
flags. The brief's evidence is that the deny list never bounded the tool set: CLI 2.1.235
exposes 23 tools under it, and `Monitor` executed a shell command with
`capabilities=None`. The docs that claimed one model turn were corrected. Default argv is
unchanged, per the brief; moving jsp's kinds off it is their B46.

Measured here (CLI 2.1.282): the CLI silently drops unknown and wrong-case `--tools`
names. That makes the exact-match `ValueError` against the ten names load-bearing, not
defensive. `browser`+`exclusive` is refused rather than proven, since no consumer needs it.

Usage is summed across `modelUsage`, and the raw dict goes in
`provider_specific_fields["model_usage"]`. This is option (c), ruled by the jsp
orchestrator over the brief's (a) sum and (b) expose, on the premiss that jsp counts
tokens against a quota and does not price them. `thinkingTokens` is excluded because
`outputTokens` already includes it (measured: the difference was the reply length on
three calls).

Verification: 82 unit tests pass. The live suite passes 6/6, including the init-event
tool-set equality for both grants and a sonnet WebSearch call whose recorded input equals
the sum of both models' `inputTokens`.

#### #0015 · completed · LCC8 · 2026-09-27
v0.5.0, from the jsp orchestrator's brief (LCC8), for jsp's `linkedin-capture` executor
(seam v218 §C, Part C §C6). Supports the mode LCC7 refused rather than proved.

**What changed.** `Capabilities` gains `mcp_servers: tuple[McpServer, ...] = ()`
(`McpServer(name, command, args)`, frozen) and `permission_prompt_tool: str | None =
None`. `exclusive=True, browser=True` is now SUPPORTED: `tools` is validated against the
new pinned `CLAUDE_IN_CHROME_TOOLS` (22 `mcp__claude-in-chrome__*` names, measured CLI
2.1.283, 2026-09-27) instead of `_DISABLED_TOOLS`, and argv carries `--chrome
[--mcp-config <json>] --strict-mcp-config --tools "" [--permission-mode default
--permission-prompt-tool <name>] --disallowed-tools <t>...` (inventory order for the
disallow tail), never `bypassPermissions`. Five new construction-time `ValueError`s:
an out-of-inventory tool in this mode; `mcp_servers`/`permission_prompt_tool` set outside
it; a `permission_prompt_tool` whose `mcp__<name>__` prefix names no configured server;
duplicate server names; a server name outside `^[a-z][a-z0-9_]*$`.

This mode runs with `--output-format stream-json --verbose` (never `json`) through a new
injectable `_StreamRunner`/`_StreamProcess` (real impl: `subprocess.Popen` + a
`threading.Timer` watchdog, since the timeout can't be `subprocess.run`'s blocking one
when the caller reads lazily). `_read_stream_result` reads the stream line by line and
fails closed — kills the process and raises `RuntimeError` naming the difference, no
`structured_output` returned — on: an `assistant` event before any `system/init` event;
an `init` event whose `tools` isn't exactly `set(capabilities.tools)` (the live proof
showed the prompt tool is NOT offered to the model, so it is never added to this set,
resolving the seam's OPEN item); an `init` event with `permissionMode != "default"` when
`permission_prompt_tool` is set; a `result` event with no preceding `init` (a
strengthening beyond the brief's literal text — reasoned below); or the stream ending
with no `init` or no `result` at all. The `result` event's line is fed straight into the
existing `_build_response`, verified against the recorded stream to have the `json`-mode
payload's shape (`is_error`, `result`, `stop_reason`, `usage`, `modelUsage`).

**Deviation from the brief:** the brief's checklist (seam v218 §C item 5) states the
fail-closed conditions as tool-set / permission-mode mismatches and "no init before the
first assistant"; it does not separately require rejecting a `result` that arrives with
no `init` at all. Added anyway: without it, a stream that skips `init` entirely but still
emits a `result` (never observed live, but not excluded by the CLI's contract either)
would silently return a resp with no init check ever having run — exactly the "silent
pass-through" the orchestrator's HIGH-worth premiss is about. Cheap to add (one
`if not init_seen` guard), so added rather than raised as a question. Flagging it here
per the brief's ask for deviations, since it's the one place this implementation is
stricter than the letter of the spec.

**Fixture.** `tests/fixtures/browser_exclusive_approver_run.jsonl`: a redacted copy of
the recorded live spike (CLI 2.1.283, `/tmp/.../scratchpad/approver/run.jsonl`), keeping
only the `system/init`, `assistant`/`user`, and `result` events (dropping
`hook_started`/`hook_response`/`thinking_tokens`/`rate_limit_event`), with `session_id`,
`cwd`, `uuid`, and path-valued fields stripped, and long strings (the thinking block's
signature, the long-form `result` text) trimmed.

**Mutation table** (brief-mandated, on `_read_stream_result`; each mutation applied by
hand, the named test run alone, output captured, then reverted — confirmed byte-identical
to pre-mutation source by `diff` before moving to the next):

| # | Mutant | Test that reddened | Observed failure |
|---|---|---|---|
| 1 | Remove the `got_tools != want_tools` branch (tool-set comparison) | `test_stream_tool_set_mismatch_kills_and_raises` | `Failed: DID NOT RAISE RuntimeError` |
| 2 | Remove the `permissionMode != "default"` branch | `test_stream_permission_mode_mismatch_kills_and_raises` | `Failed: DID NOT RAISE RuntimeError` |
| 3 | Remove the `proc.kill()` call in the tool-set-mismatch branch (raise kept) | `test_stream_tool_set_mismatch_kills_and_raises` | `assert False` on `fake_proc.killed` (via `AssertionError` at the trailing `assert fake_proc.killed`) |

**Verification:** `uv run pytest -q` → 119 passed, 6 skipped (the pre-existing
`RUN_LIVE_SMOKE=1`-gated live suite; this brief forbids running `claude -p --chrome`
live, so it stays untouched and unrun). `uv run ruff check .` and `uv run ruff format
--check .` clean. `uv run mypy src tests` — zero new errors; the 22 remaining are the
pre-existing baseline in `test_provider.py`/`test_live_smoke.py` (confirmed identical via
`git stash`), unrelated to this change. Every pre-LCC8 test in `test_capabilities.py`,
`test_provider.py`, `test_litellm_dispatch.py` and `test_live_smoke.py` passes unmodified
except `test_exclusive_refuses_the_browser`, rewritten to `
test_exclusive_and_browser_together_is_now_supported` (the refusal it pinned is exactly
what this release lifts) — plus two new full-argv pins
(`test_browser_only_mode_full_argv_pinned`, `test_exclusive_only_mode_full_argv_pinned`)
added alongside the pre-existing `test_capabilities_none_leaves_the_real_argv_untouched`,
so all three non-browser-exclusive modes now have a byte-identity pin, per the brief's
item 7.

#### #0016 · amended · LCC8 · 2026-09-27
Orchestrator review caught a defect in #0015's fixture: this repo is PUBLIC, and
`tests/fixtures/browser_exclusive_approver_run.jsonl`'s `system/init` line carried
Chris's personal environment verbatim — 4 plugins, 50 skills, 86 slash commands, 6
agents, plus `memory_paths`, `output_style`, and `messaging_socket_path`. #0015's own
redaction pass (session_id/cwd/uuid/paths stripped, long strings trimmed) never touched
these list- and account-shaped fields, because "trim long text fields" was read as
literal text fields, not lists — the exact kind of near-miss a whitelist check would
have caught earlier.

Fixed by rebuilding the `init` line as an explicit whitelist rather than a blacklist:
kept only `type`, `subtype`, `tools`, `mcp_servers` (reduced to `name`+`status`, dropping
`source`), `model`, `permissionMode`, and `claude_code_version` — every other key
(`plugins`, `skills`, `slash_commands`, `agents`, `terminal_slash_commands`,
`memory_paths`, `output_style`, `messaging_socket_path`, `capabilities`,
`apiKeySource`, `analytics_disabled`, `product_feedback_disabled`, `fast_mode_state`,
`fast_mode_disabled_reason`, `per_turn_effort_active`, `view_mode`) dropped outright.
The other 18 lines (assistant/user/result) were unaffected — none carried
plugin/skill/account data.

Verified: `grep -inoE "chris|/home|swiftwater|@|drive|docs"
tests/fixtures/browser_exclusive_approver_run.jsonl` — no matches (all four hit terms
lived only inside the now-dropped `plugins`/`slash_commands`/`skills` lists). Also
grepped for email-shaped strings and `/…home…` paths generally — none. `uv run pytest
-q` → 119 passed, 6 skipped, unchanged from #0015 (nothing in the test suite reads the
dropped fields).

#### #0017 · amended · LCC8 · 2026-09-27
Fix round 2, from an independent Opus review (full text: LCC8 review, reviewed at head
`422f696`) that weighted the real streaming runner as heavily as the checks: "kill() was
called" is not the fence, "the process stopped" is, and nothing tested that. Verdict
APPROVE-WITH-FIXES; F2 (the unscrubbed fixture still in `6eb57a3`'s history) is disposed
by the orchestrator's squash at release, not fixed here. Every other finding addressed:

- **F1 — any exception kills the process.** `_read_stream_result`'s whole loop now runs
  under one `try: … except BaseException: proc.kill(); raise`, replacing the per-branch
  `proc.kill()` calls the mutants (R1–R3, prior round) had targeted. A non-object JSON
  line (`[1,2]`) now raises a named `RuntimeError` instead of an uncaught
  `AttributeError`; a genuinely malformed line (`json.JSONDecodeError`) is left
  unclassified but still kills via the catch-all.
- **F3 — the actuator is now tested for real.** New file
  `tests/test_stream_runner_process.py` points the REAL `_default_stream_runner` at a
  small fake CLI script (never `claude`, never Chrome) and asserts the OS process is
  dead via `poll()`/`os.kill(pid, 0)` after: a failed init check, a malformed line, and
  the watchdog timeout. Mutants C (`kill()` a no-op) and D (timer never started) both
  reddened — table below.
- **F4 — the fixture's real Chrome pairing device ID** (`<real device id, redacted>`,
  appearing twice: `input.deviceId` and `wire_tool_inputs`) replaced with
  `00000000-0000-4000-8000-000000000001`. Nothing asserts on the value. Re-grepped the
  fixture for any 36-char UUID: the only match left is the two occurrences of the dummy
  one just inserted — justified as the intentional placeholder, nothing else remains.
- **F5 — a missing or null `tools` key now fails closed unconditionally.**
  `set(event.get("tools") or [])` used to fold both into `set()`, which equalled
  `set(())` and passed when the grant was empty. Now `isinstance(event.get("tools"),
  list)` is required before the equality check runs at all, so a missing/null key fails
  regardless of the grant. Three new tests, including the exact `tools=()` case named in
  the finding.
- **F6 — timeout and exhaustion are classified like every other mode.** Stderr is
  captured to a bounded temp file (not a pipe, to avoid a deadlock behind a lingering
  child) rather than `DEVNULL`. `_PopenStreamProcess` tracks whether its own watchdog
  fired (`timed_out`); on EOF with no result, `_read_stream_result` calls `proc.finish()`
  (kills defensively, returns `(returncode, stderr)`) and raises
  `subprocess.TimeoutExpired` if the watchdog fired, else runs `_exhaustion_error` over
  the captured stderr exactly as `_default_runner` does over combined stdout+stderr,
  raising `ClaudeExhausted` on a match or a plain `RuntimeError` naming the exit code
  otherwise. Tested against both the fake-process double (fast, classification-focused)
  and the real runner (`test_real_runner_exhaustion_from_stderr_after_nonzero_exit`,
  `test_real_runner_watchdog_kills_on_timeout`).
- **F7 — process-GROUP kill, and reaping on every path.** The child now starts in its
  own session (`start_new_session=True`), and `kill()` sends `SIGKILL` via
  `os.killpg(pid, …)` (falling back to a direct signal if the pid is already gone),
  so an MCP server the CLI spawned dies with it —
  `test_real_runner_kills_the_grandchild_too` proves a `sleep 60` grandchild dies.
  The success path calls a new `reap(timeout=5.0)` (bounded wait, then kill if it
  hasn't exited) before returning the result line, so a clean call doesn't leave a
  zombie either.
- **F8 (RULING, the orchestrator's) — `--permission-mode default` is now
  UNCONDITIONAL** in browser+exclusive mode, and the init's `permissionMode` check runs
  regardless of whether `permission_prompt_tool` is set. Closes the gap where a call
  with no approver inherited a settings file's own `defaultMode` (including
  `bypassPermissions`) and the provider accepted it. `_browser_exclusive_argv` and
  `_read_stream_result` updated; the old "iff `permission_prompt_tool`" tests rewritten
  (`test_stream_permission_mode_checked_even_without_a_prompt_tool`,
  `test_permission_mode_default_always_present_but_prompt_tool_flag_is_not`, plus a new
  `bypassPermissions` case).
- **F9 — the two missing tests.** `test_stream_init_offers_fewer_tools_than_granted_fails_closed`
  (a strict subset of the grant) and `test_stream_second_init_with_wrong_tools_fails_closed`
  (a bad SECOND init after a good first one). Both added; mutants A
  (`got_tools - want_tools`) and B (`… and not init_seen`) both reddened — table below.
- **F10 — wording.** Every "before the first model turn" claim (module docstring,
  `_read_stream_result`'s docstring, README, CHANGELOG) replaced with: no result is ever
  accepted without an init that passed both checks, and a failing init is killed as soon
  as it is read — explicitly NOT a guarantee about when relative to the model's first
  request or tool call, since the CLI may already have dispatched one by the time the
  kill lands.

**New mutation table** (this round; the prior round's R1–R3 were re-run against the
restructured source first, to confirm the redesign didn't quietly un-prove them — R1 and
R2 still redden identically; R3 no longer applies as a distinct mutant, since its target
— a per-branch `proc.kill()` before one specific `raise` — no longer exists: killing is
now centralized in F1's catch-all, which IS the new mutant below). Each mutation applied
by hand (`Edit`), the named test(s) run alone, output captured, then reverted and
confirmed byte-identical via `diff` before the next mutation. Real-runner mutations
(C, D) leave a live OS process on a red run; each was killed and confirmed gone
(`ps aux | grep fake_cli.py`) before reverting.

| # | Mutant | Test that reddened | Observed output |
|---|---|---|---|
| R1 (re-run) | Tool-set comparison removed | `test_stream_tool_set_mismatch_kills_and_raises` | `Failed: DID NOT RAISE RuntimeError` |
| R2 (re-run) | permissionMode comparison removed | `test_stream_permission_mode_mismatch_kills_and_raises` | `Failed: DID NOT RAISE RuntimeError` |
| F1 | `except BaseException: raise` (kill removed from the catch-all) | `test_real_runner_kills_the_process_on_a_malformed_line` | `assert False` on `_wait_until_dead(_pid_of(proc))`; PID `1663247` confirmed alive, killed manually, re-verified gone |
| C | `_PopenStreamProcess.kill` made a no-op (`return` before the body) | `test_real_runner_kills_the_process_on_a_failed_init_check` | `assert False` on `_wait_until_dead(_pid_of(proc))`; PID `1659515` confirmed alive, killed manually, re-verified gone |
| D | `self._timer.start()` commented out in `__init__` | `test_real_runner_watchdog_kills_on_timeout` | Took the fake CLI's full 60 s instead of ~0.3 s, then raised `RuntimeError: … stream ended without a system/init event` instead of `subprocess.TimeoutExpired` — test failed, 62.42 s |
| A | `if got_tools != want_tools:` → `if got_tools - want_tools:` (subset tolerated) | `test_stream_init_offers_fewer_tools_than_granted_fails_closed` | `Failed: DID NOT RAISE RuntimeError` |
| B | Init check gated on `… and not init_seen` (only first init checked) | `test_stream_second_init_with_wrong_tools_fails_closed` | `Failed: DID NOT RAISE RuntimeError` |

All seven mutations reddened exactly their targeted test and only that test; source
restored and `diff`-confirmed identical after each.

**Verification:** `uv run pytest -q` → 136 passed, 6 skipped (up from 119; +11 in
`test_browser_exclusive.py` for F5/F8/F9/F1-fake-process coverage and permission-mode
rewrites, +6 in the new `tests/test_stream_runner_process.py` for F3/F6/F7). `uv run
ruff check .` and `uv run ruff format --check .` clean. `uv run mypy src tests` — 22
errors, identical to the pre-existing `test_provider.py`/`test_live_smoke.py` baseline,
zero new. No leftover fake-CLI processes after the run (`ps aux | grep fake_cli.py`
checked clean).

**Deviation:** none beyond what's listed above as F8 (an explicit ruling, not a
deviation) and the F1 catch-all superseding R3 as a mutation target (noted above, not a
scope change).

#### #0018 · amended · LCC8 · 2026-09-27
Fix round 3, from a scoped re-review of `f1f14b2`/`fb0acb9` ("Round 2" in the LCC8
review): "Not ALL ADDRESSED" — two items left open, N1 and N2 (F2, the four-commit
squash, stays the orchestrator's action at release).

- **N1 — the captured-stderr temp file leaked on every path except `finish()`.**
  Measured by the reviewer: the success path (`reap()`, then `return`) and the
  check-failure path (`kill()` in the `except`) never touched the file; only the
  EOF-without-a-result path through `finish()` unlinked it. Fixed by moving deletion out
  of `finish()` entirely and into a new `_StreamProcess.cleanup()`, called exactly once
  from a `finally` wrapped around `_read_stream_result`'s whole `try/except`, so every
  return and every raise — including ones the `except` clause re-raises — passes through
  it. `cleanup()` is idempotent (`self._stderr_path = None` after unlinking) and safe
  whether or not `finish()` ran. `_default_stream_runner` gained an optional `stderr_dir`
  parameter (not used by the provider itself) so tests can inject an isolated directory
  to snapshot rather than sharing `/tmp` with everything else on the machine. Three new
  tests in `tests/test_stream_runner_process.py` snapshot that directory after the
  success path, a failed init check, and a malformed line, asserting no `*.stderr` file
  remains in each case.
- **N2 — non-JSON stream output surfaced as `json.JSONDecodeError`, a `ValueError`
  subclass.** This module's own taxonomy reserves `ValueError` for caller error (see
  `_encode_schema_arg`), and `_build_response` raises `RuntimeError` for the identical
  non-JSON-output situation in `json` mode. Fixed: `json.loads(line)` in
  `_read_stream_result` is now wrapped in `except json.JSONDecodeError as exc: raise
  RuntimeError(...) from exc`. The kill behaviour is unchanged — the `RuntimeError` still
  flows through the same `except BaseException: proc.kill(); raise` as before. Three
  tests updated to pin the new type: `test_stream_runner_process.py`'s
  `test_real_runner_kills_the_process_on_a_malformed_line` and
  `test_real_runner_kills_the_grandchild_too` (the two the dispatch named), plus
  `test_browser_exclusive.py`'s `test_stream_malformed_json_line_kills_and_propagates`
  (renamed `…_and_raises_runtime_error`), which the dispatch didn't name but pinned the
  same old type and would otherwise have gone red.

**Mutation-proving N1** (applied by hand, the three new tests run together, reverted,
`diff`-confirmed identical): `cleanup()`'s body replaced with an unconditional `return`
before the delete. Result: all three reddened —
`test_real_runner_success_path_leaves_no_stderr_tempfile`,
`test_real_runner_failed_init_check_leaves_no_stderr_tempfile`,
`test_real_runner_malformed_line_leaves_no_stderr_tempfile` each failed with
`AssertionError: assert [PosixPath('.../<name>.stderr')] == []`, i.e. the file the
mutation left behind. Restored, `diff` clean, all three green again. N2 was not
separately mutation-proven — the dispatch asked for that specifically on N1, and N2's
"before" state is exactly the prior (already-tested) behaviour the three updated test
pins moved off of, so there's nothing further a mutation would demonstrate.

**Verification:** `uv run pytest -q` → 139 passed, 6 skipped (up from 136: +3 for N1).
`uv run ruff check .` and `uv run ruff format --check .` clean. `uv run mypy src tests`
— 22 errors, the same pre-existing `test_provider.py`/`test_live_smoke.py` baseline, zero
new. No leaked `*.stderr` files and no orphan `fake_cli.py` processes after the full run
(`find /tmp/pytest-of-chris -name '*.stderr'`, `ps aux | grep fake_cli.py`, both checked
empty).

