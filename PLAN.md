# PLAN

Current state only — ordered `Next` (with deps) + recent `Done`. History and reasons live in
`ACTION_LOG.md`; superseded/discarded items move to `_archive/ARCHIVED_PLAN.md`. Task IDs use this
repo's prefix `LCC` (see the PI implementer registry). Per `pi-convention.md`.

## Next

_(nothing queued)_

## Done
- [x] LCC6 — `--disable-slash-commands` on every call: fixed argv, skills closed (v0.3.2) → log:#0012
- [x] LCC5 — Configurable call timeout: `timeout=` on `ClaudeCliLLM`, `_Runner` gains the keyword (v0.3.1) → log:#0011
- [x] LCC4 — First-class capabilities: `Capabilities` param, argv built from it, `tool_use`→`stop` re-keyed (v0.3.0) → log:#0009
- [x] LCC3 — Structured output via `--json-schema` + `structured_output` on the response → log:#0006
- [x] LCC2 — Register litellm-claude-cli in the patterns implementer registries (PI/MEMORY/Git/Docs-layout) → log:#0004
- [x] LCC1 — Adopt patterns conventions (PI, Committed Memory, Git, Docs-layout) → log:#0002
