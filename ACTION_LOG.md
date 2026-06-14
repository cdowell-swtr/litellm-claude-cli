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
