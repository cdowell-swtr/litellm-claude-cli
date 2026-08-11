---
name: uv-lock-self-version-drift
description: uv.lock records this package's own version and goes stale on every release — run `uv lock` when bumping
scope: project
metadata:
  type: project
---

`uv.lock` contains an entry for `litellm-claude-cli` itself carrying its `version`. Bumping
`pyproject.toml` does **not** update it, so it silently drifts one release behind and shows up
later as a mystery uncommitted diff. It was stale at `0.1.0` right through the `0.1.1` release and
was still stale when `0.2.0` shipped.

Add `uv lock` to the version-bump step and commit the result. It is a one-line diff with no
dependency churn.

Dev-only: consumers install from the git tag and resolve through `pyproject.toml`, so a stale lock
never reaches them — which is exactly why nobody notices. Do **not** move a release tag to pick up
a lock-only fix. Related: [[release-tag-after-squash-merge]].
