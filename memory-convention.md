<!-- MEMORY-convention: v1 -->
# Committed Memory — Convention

*Authoritative definition. Version: `MEMORY-convention: v1`. Claude-Code-specific (binds to CC's memory format + autoload). Portable across repos and machines; depends on git, markdown, and CC's `CLAUDE.md` `@`-import.*

## Purpose
Give a repo a committed, project-scoped memory an agent autoloads every session, on every machine — **alongside** the native machine-local store, split by a public-safety boundary. The native store is machine-local and uncommitted (it doesn't travel to other machines, teammates, or fresh clones); project knowledge that everyone should start with does.

## The artifacts
| File | Holds | Read temperature |
|---|---|---|
| `MEMORY.md` (root) | The **index** — one line per memory: `- [Title](_memory/slug.md) — hook` | hot — autoloaded every session (via `CLAUDE.md` `@`-import) |
| `_memory/<slug>.md` | **One memory per file**: a single durable fact, frontmatter + body; `[[slug]]` resolves to `_memory/<slug>.md` | warm — read on relevance |
| `CLAUDE.md` (`@MEMORY.md`) | Native CC import that autoloads the index into every session. No bespoke hook. | — (mechanism) |
| native store `~/.claude/.../memory/` (uncommitted) | Machine / personal / sensitive memories; anything that fails the boundary. Keeps its native autoload. | hot (machine-scoped) |

## The public-safety boundary (core rule)
A memory belongs in the committed store **when, and only when, BOTH** of these hold; if either fails, it belongs in the native (uncommitted) store:

1. **Useful to anyone working this repo on any machine** — about *the project*, not about you, your machine, or one session.
2. **Safe to publish** — this repo may have a public remote, so a commit is an irreversible publish (git history persists even after deletion).

**Safe-to-publish heuristics.**
- *Commit-safe examples:* project conventions and naming rules; architecture/decision summaries already reflected in the repo; build/test/run commands; gotchas about the codebase; pointers to public docs/issues.
- *Never commit* (keep native, or don't record at all): secrets, passwords, keys, tokens, credentials, connection strings; internal hostnames/IPs/private URLs; **PII about anyone** — full names tied to identities, emails/phone numbers/addresses, government identifiers (SSNs, tax IDs, passport/license numbers); company, customer, client, or employer names and other org identifiers, plus private contract/pricing detail; unfixed security vulnerabilities; anything embargoed or under NDA; machine-specific absolute paths that expose a username or private layout.

**When in doubt, stay native.** The failure modes are asymmetric: a wrongly-native memory merely isn't shared (cheap, reversible); a wrongly-published one is leaked forever (expensive, irreversible).

`scope: project` frontmatter marks eligibility and makes committing a conscious act. The committed store **complements, never replaces** the native one. gitleaks (below) is the backstop for when this rule is *misapplied* — not a substitute for applying it.

## File format (native frontmatter + body)
```
_memory/<slug>.md:
  ---
  name: <slug>
  description: <one-line summary — used at recall time to judge relevance>
  scope: project                 # REQUIRED for the committed store
  metadata: { type: project | feedback | reference }
  ---
  <the single fact; link related COMMITTED memories with [[other-slug]]>

MEMORY.md (index — one line per memory):
  - [Title](_memory/slug.md) — one-line hook
```
Identical to the native memory format save the required `scope:` field — so migrating a native memory in is a near-copy.

## Autoload — native, no hook
Add `@MEMORY.md` to the repo's `CLAUDE.md`. Claude Code imports the index into every session automatically; because `CLAUDE.md` is committed, it travels to every clone/machine with zero setup. Bodies are read on relevance via `[[slug]]`. No `SessionStart` hook is required.

## Invariants you must uphold (no validator does it for you)
Before finishing any session that touched the committed store, confirm:
- **Index ↔ files bidirectionally complete:** every `_memory/<slug>.md` is listed in `MEMORY.md`, and every index entry points at a real file.
- Every `[[slug]]` resolves to a real `_memory/` file. **A cross-store `[[slug]]` to a native-only memory is NOT allowed — reword it to prose.**
- Required frontmatter present: `name`, `scope: project`.
- One durable fact per file.

## Secret backstop (required, wired before any memory is committed)
A public commit is irreversible, so run **gitleaks** via the **pre-commit framework** (the mechanism swiftwater-framework already uses — don't hand-roll a hook that calls a bare `gitleaks`, which may not be on PATH):
- **Local:** a `.pre-commit-config.yaml` pinning the `gitleaks/gitleaks` hook; `pre-commit install` wires it into `.git/hooks` (`default_install_hook_types: [pre-commit, pre-push]`). pre-commit manages the gitleaks binary, so there's no PATH/install fuss; a staged secret is blocked before it lands.
- **CI:** a workflow running a full-repo gitleaks scan on push/PR — the memory-independent backstop (a skipped local hook still can't land a leak).
This scans for leaks; it does not replace the boundary rule.

## Adopt in a repo (from zero)
1. Create `MEMORY.md` (empty index) + `_memory/` at the repo root.
2. Add `@MEMORY.md` to `CLAUDE.md` (create it if absent), plus the one-line boundary note and the `MEMORY-convention: v1` marker (block below).
3. **Wire gitleaks first** — add a `.pre-commit-config.yaml` with the `gitleaks` hook + run `pre-commit install`, plus a CI gitleaks scan. Do this before committing any memory content.
4. Seed: migrate the public-safe, project-scoped subset of your native store into `_memory/` (near-copy: add `scope: project`; drop anything failing the boundary); build `MEMORY.md`; repair any cross-store `[[links]]`; scan with gitleaks before committing.
5. **Register yourself** — append a row to `_docs/committed-memory/implementers.md` in the home repo (`patterns`): `<repo> | <local path, e.g. `~/projects/<repo>` — use `~`, not `/home/...`> | v1 | <date>`.
6. Route every new memory by the boundary: both hold → committed; else native; when in doubt → native.

Find the registry / adopters / synced versions: `grep -rIn "MEMORY-convention:" <your projects root>`.

### CLAUDE.md block (copy this)
```
<!-- MEMORY-convention: v1 -->
## Committed project memory
Project memory is autoloaded from `MEMORY.md` (imported below). Resolve `[[slug]]` to `_memory/<slug>.md`.
Commit a memory only when it is BOTH useful to anyone working this repo AND safe to publish; otherwise keep
it in the native store. When in doubt, native. Full rule + never-commit list in `memory-convention.md`.

@MEMORY.md
```
