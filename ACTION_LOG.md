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

