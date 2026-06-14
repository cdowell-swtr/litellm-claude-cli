---
name: ci-docs-layout-vendored-script
description: CI must run the vendored docs-layout-check.sh, not `pre-commit run docs-layout`
scope: project
metadata:
  type: project
---

In GitHub Actions, the docs-layout validator must run as `bash hooks/docs-layout-check.sh`
(vendored from cdowell-swtr/patterns at tag `docs-layout/v1`) — **not** `pre-commit run docs-layout`.
The pre-commit hook resolves docs-layout from the *private* `cdowell-swtr/patterns` repo, and the
default Actions `GITHUB_TOKEN` can't clone a different private repo, so pre-commit fails with
`could not read Username for 'https://github.com'` (exit 3). Local dev still uses the pre-commit
hook (devs have their own git/gh auth); CI uses the vendored script. The adoption runbook documents
this split. gitleaks in CI installs the binary directly (matching the patterns repo's reference
workflow) for the same independence reason.
