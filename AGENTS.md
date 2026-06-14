# AGENTS.md

Canonical, portable agent guidance for this repo. Claude Code autoloads `CLAUDE.md`, which imports
this file via `@AGENTS.md`. Adopted conventions are pulled from `cdowell-swtr/patterns`; the vendored
spec for each lives at the repo root (`pi-convention.md`, `git-convention.md`, `docs-layout-convention.md`).

<!-- PI-convention: v2 -->
## Planning Instrument
Read `PLAN.md` first. Maintain `PLAN.md` + `ACTION_LOG.md` at task grain as you work
(tick tasks; append a log entry on every completion and every deviation), per `pi-convention.md`.
Task IDs use this repo's prefix (see the implementer registry).

<!-- GIT-convention: v1 -->
## Git
Branches: `<task-id>-<slug>` (1:1 to a PLAN item) or `<type>/<slug>` fallback; direct-to-main OK for solo/small repos.
Commits: Conventional Commits `type(scope): subject` (+ `Co-Authored-By` for agents). Tags: `<thing>/vN`.
Write to other repos via a clone/PR, never their live working copy; never run two sessions in one working copy.
Full rule: `git-convention.md`.

<!-- DOCS-convention: v1 -->
## Documentation layout
Internal docs in `_docs/`, external in `documentation/`. Namespace internal docs per concern
(`_docs/<namespace>/`). A planning tool's specs/plans go in `_docs/<namespace>/<tool>/{specs,plans}/`
(dated `YYYY-MM-DD-<name>.md`) — NOT the tool's default (e.g. superpowers' `docs/superpowers/...`);
drop a `.tool` file naming the tool in each tool dir. Full rule: `docs-layout-convention.md`.
