---
name: ci-runs-on-every-branch
description: CI triggers on pushes to every branch because a release once bypassed it entirely by landing on a stray branch
scope: project
metadata:
  type: project
---

`.github/workflows/ci.yml` triggers on `push: branches: ['**']`, not just `master`. Do not
narrow it back. The filter used to be `[master]`, and v0.3.1 slipped through the gap: it was
committed to a stray branch — created by accident, tracked by no clone, deleted once found —
so no push event matched and no PR existed to fire the `pull_request` trigger. CI ran **zero times** on that commit. It was
tagged, published, and installed by the downstream consumer while `ruff format --check` was
failing on it — discovered only when `master` was fast-forwarded onto it days later and the next
PR inherited the red build (repaired in `066598a`, formatting only).

Two independent failures had to line up, and both are worth recognising by shape:

- **A release on a branch no tooling watched.** `master` is this repo's default branch and
  always has been; the stray branch was never canonical and no longer exists. But while it did,
  a fresh clone got a tree without the release, and `git tag` locally showed the previous
  version as newest until `git fetch --tags` was run explicitly. **When a version number or tag
  seems not to exist, fetch tags before concluding it doesn't** — the local view is not
  authoritative. Here that mistake was caught only because the downstream consumer pinned a
  commit hash that could not be found; without that push-back a second, conflicting tag of the
  same version would have been published.
- **A green history that proves nothing.** Every other commit passed, so the branch looked
  healthy. Absence of a red build is not evidence CI ran.

`master` also carries branch protection requiring the `ci` check, which closes the merge path.
The trigger widening is what closes the direct-push path — protection on one branch cannot
defend a branch that does not exist yet. Related: [[release-tag-after-squash-merge]].
