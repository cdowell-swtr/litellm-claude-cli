# Committed project memory

Index of committed, project-scoped memories — one line per memory: `- [Title](_memory/slug.md) — hook`.
Bodies live in `_memory/<slug>.md` and are read on relevance; resolve `[[slug]]` to `_memory/<slug>.md`.
Commit a memory only when it is BOTH useful to anyone working this repo AND safe to publish — otherwise
keep it in the native store. Full rule: `memory-convention.md` (MEMORY-convention: v1).

- [CI docs-layout uses vendored script](_memory/ci-docs-layout-vendored-script.md) — CI runs `hooks/docs-layout-check.sh`, not `pre-commit run docs-layout` (private-repo clone fails in Actions)
