<!-- GIT-convention: v1 -->
# Git Convention

*Authoritative definition. Version: `GIT-convention: v1`. Git practice for these projects — branch naming, commit messages, tags, workflow, single-writer working copy. Consumer-adoptable. Enforcement: commit messages (Conventional Commits) + secret scanning (gitleaks); the rest is agent-upheld.*

## Purpose
Consistent, low-friction git practice across our repos — branches/commits/tags self-describe, history stays machine-usable, and concurrent work doesn't corrupt shared state.

## Branch naming
- **1:1 to a PLAN item** when one exists (PI repo, branch workflow): `<task-id>-<slug>` — e.g. `pat18-convention-playbook`, `mrdn4-edr-schema`.
- **Fallback** when there's no 1:1 task: `<type>/<slug>` — `fix/`, `chore/`, `spike/`, `register/`, `docs/`, `refactor/` (mirrors commit types).
- **Direct-to-main is fine** for solo/small/low-risk repos. The 1:1 mapping is the *ideal for task-branches*, not a mandate.

## Commit messages — Conventional Commits (enforced)
`type(scope): subject` — types `feat fix docs chore refactor test build ci perf style`; scope free-form (often the PI task-id or a module). **Enforced** by `conventional-pre-commit` at the `commit-msg` stage (see Enforcement). Agent commits append a `Co-Authored-By:` trailer. Attribution lives in the commit/PR — **never in convention content**.

## Tags
Annotated `<thing>/vN`, semver-lite (`pi/v2`, `docs-layout/v1`). Published conventions are pulled from these tags (see `CONVENTIONS.md`).

## Workflow
- **Direct-to-main** for solo/small/low-risk work.
- **PR** for shared, cross-repo, or risky writes — made **from a clone or `gh`, never the live working copy of the target repo**. Registration is a one-line PR. PR bodies carry the 🤖 generated-with trailer.

## Single-writer working copy
Never run two agents/sessions in the same working copy concurrently, and never write to another repo by operating its live working copy — clone or use `gh`. Concurrent writers corrupt shared state (duplicate monotonic IDs, thrashed refs). Mechanically unenforceable; uphold it.

## Grain
Commits may be fine-grained; PLAN ticks + `ACTION_LOG` entries stay at task grain (see the PI convention — not re-derived here).

## Enforcement (commit boundary)
Two hooks run at the commit boundary — wire both in `.pre-commit-config.yaml`, then `pre-commit install`:
```yaml
- repo: https://github.com/gitleaks/gitleaks
  rev: v8.21.2
  hooks:
    - id: gitleaks
- repo: https://github.com/compilerla/conventional-pre-commit
  rev: v3.6.0
  hooks:
    - id: conventional-pre-commit
      stages: [commit-msg]
```
Ensure `default_install_hook_types` includes `commit-msg`. **Secret scanning** (gitleaks) blocks secrets before they land — a commit can publish, irreversibly. **Commit messages** are validated against Conventional Commits. Branch naming, workflow, and single-writer remain agent-upheld (not gated).

## When to adopt
Broadly applicable — git practice is universal, and the direct-to-main blessing means even a solo repo complies trivially. Only the 1:1-branch ideal is PI-specific (the fallback covers non-PI repos). No grandfathering needed.

## Adopt in a repo
1. Pull this convention from the latest `git/v*` tag (see `_docs/git/adoption-runbook.md`).
2. Wire both commit-boundary hooks — gitleaks + `conventional-pre-commit` (block above); `pre-commit install`.
3. Add the AGENTS.md rule block (below); for Claude Code, ensure `@AGENTS.md` in `CLAUDE.md`.
4. Register by PR to `cdowell-swtr/patterns` (`_docs/git/implementers.md`).

Find adopters / versions: `grep -rIn "GIT-convention:" <your projects root>`.

### AGENTS.md rule (copy this block)
```
<!-- GIT-convention: v1 -->
## Git
Branches: `<task-id>-<slug>` (1:1 to a PLAN item) or `<type>/<slug>` fallback; direct-to-main OK for solo/small repos.
Commits: Conventional Commits `type(scope): subject` (+ `Co-Authored-By` for agents). Tags: `<thing>/vN`.
Write to other repos via a clone/PR, never their live working copy; never run two sessions in one working copy.
Full rule: `git-convention.md`.
```
