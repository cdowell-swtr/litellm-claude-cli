<!-- DOCS-convention: v1 -->
# Documentation Layout — Convention

*Authoritative definition. Version: `DOCS-convention: v1`. A cross-project standard for where documentation lives, with a zero-dep validator shipped as a pre-commit hook. Depends on git, a POSIX shell, and pre-commit.*

## Purpose
Separate internal docs from external, namespace internal docs per concern, and give each concern's planning artifacts a tool-namespaced, explicitly-marked home — so docs don't sprawl and agent-driven placement doesn't drift.

## When to adopt (applicability)
Adoption is **all-or-nothing per repo**: the validator scans the whole `_docs/` tree, so a repo either meets the layout (adopt, validator green) or stays out (don't wire it — legacy untouched). There is **no partial or forward-only mode**. So this convention suits **greenfield or light-docs repos**, where honoring the layout costs nothing. A **mature repo with entrenched, densely cross-linked docs should not adopt** — the link-remapping migration outweighs the consistency gain. Judge it on fit/cost, not universality.

## Layout
| Path | Holds |
|---|---|
| `_docs/` | internal docs (underscore = unpublished) |
| `_docs/<namespace>/` | per-concern dir; may hold dateless artifacts + non-tool subdirs (diagrams, decisions, reference, assets) |
| `_docs/<namespace>/<tool>/.tool` | marker — this dir is tool-managed (content: the tool name) |
| `_docs/<namespace>/<tool>/specs/` | a planning tool's specs — dated `YYYY-MM-DD-<name>.md` |
| `_docs/<namespace>/<tool>/plans/` | a planning tool's plans — dated `YYYY-MM-DD-<name>.md` |
| `documentation/` | external / published docs (structure unconstrained) |

`<tool>` = whatever planning skill produced the artifacts (superpowers = exemplar), flagged by a `.tool` marker. Non-tool subdirs (no marker) are unconstrained. Other top-level doc dirs are allowed.

## Rules (what the validator enforces)
- **Tool dirs marked + placed** — a dir with a `.tool` file sits at `_docs/<namespace>/<tool>/`; every `specs`/`plans` dir lives inside a marked tool dir.
- **No bare `docs/`** — internal docs use `_docs/`. *(If external tooling mandates `docs/`, opt out of this check.)*
- **Dated filenames** for `*.md` inside a marked tool dir's `specs`/`plans`. Everything outside marked tool dirs is exempt (dateless, free-form).

## Enforcement — pre-commit hook (zero-dep bash)
The validator (`hooks/docs-layout-check.sh`) is published as a pre-commit hook from the `patterns` repo:
```yaml
# .pre-commit-config.yaml
- repo: https://github.com/cdowell-swtr/patterns
  rev: docs-layout/v1
  hooks: [{ id: docs-layout }]
```
`pre-commit install` wires it; run `pre-commit run docs-layout --all-files` (or `bash hooks/docs-layout-check.sh`) in CI. Private repo → pre-commit clones with your git auth.

## AGENTS.md rule (copy this block)
```
<!-- DOCS-convention: v1 -->
## Documentation layout
Internal docs in `_docs/`, external in `documentation/`. Namespace internal docs per concern
(`_docs/<namespace>/`). A planning tool's specs/plans go in `_docs/<namespace>/<tool>/{specs,plans}/`
(dated `YYYY-MM-DD-<name>.md`) — NOT the tool's default (e.g. superpowers' `docs/superpowers/...`);
drop a `.tool` file naming the tool in each tool dir. Full rule: `docs-layout-convention.md`.
```

## Adopt in a repo
See the adoption runbook (`_docs/docs-layout/adoption-runbook.md`): pull from the latest `docs-layout/v*` tag, wire the hook + CI, add the AGENTS.md rule (surface via `@AGENTS.md` in `CLAUDE.md`), move docs into the layout + mark tool dirs, and register by PR.

Find adopters / versions: `grep -rIn "DOCS-convention:" <your projects root>`.
