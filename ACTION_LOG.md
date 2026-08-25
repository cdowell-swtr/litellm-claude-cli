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

#### #0007 · inserted · LCC4 · 2026-08-24
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
