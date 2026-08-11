---
name: release-tag-after-squash-merge
description: Create the vN.N.N tag on master AFTER the PR squash-merges, never on the feature branch
scope: project
metadata:
  type: project
---

PRs in this repo are **squash-merged** (see `#1`, `#2`), so a feature branch's commits never
become ancestors of `master` — the squash produces a brand-new commit. A tag created on the
branch before merging therefore points at a commit that is not on `master`, and the branch that
carried it is deleted on merge.

Release order: merge the PR first, sync local `master`, verify the merged result, **then**
`git tag -a vN.N.N master` and push the tag. `v0.2.0` was created on the branch first and had to
be deleted and recreated twice before it landed on `b8aabb5`.

The consequence is not cosmetic: the downstream consumer installs by git tag
(`litellm-claude-cli @ git+…@vN.N.N`), so a tag pointing off-`master` publishes code that the
repo's own history does not contain. Related: [[uv-lock-self-version-drift]].
