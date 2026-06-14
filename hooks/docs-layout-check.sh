#!/usr/bin/env bash
# docs-layout validator (DOCS-convention). Zero-dep; scans the doc tree only.
# C1: tool dirs marked (.tool) + placed at _docs/<ns>/<tool>/; specs|plans live inside a marked tool dir.
# C2: no bare top-level docs/ (internal docs use _docs/, external use documentation/).
# C3: *.md inside a marked tool dir's specs|plans are dated YYYY-MM-DD-<name>.md.
set -uo pipefail
fail=0
report() { echo "docs-layout: $1" >&2; fail=1; }

# C2 — no bare docs/ at repo root
[ -d docs ] && report "bare 'docs/' present — internal docs belong in '_docs/' (external in 'documentation/')"

[ -d _docs ] || exit "$fail"   # no internal docs tree: only C2 applied

# C1a — every .tool marker sits at _docs/<ns>/<tool>/.tool
while IFS= read -r marker; do
  dir=${marker%/.tool}
  [[ "$dir" =~ ^_docs/[^/]+/[^/]+$ ]] || report "tool marker at wrong depth: $marker (want _docs/<namespace>/<tool>/.tool)"
done < <(find _docs -type f -name .tool)

# C1b — every specs|plans dir is at the right depth AND inside a marked tool dir
while IFS= read -r d; do
  if [[ ! "$d" =~ ^_docs/[^/]+/[^/]+/(specs|plans)$ ]]; then
    report "misplaced '$(basename "$d")': $d (want _docs/<namespace>/<tool>/{specs,plans})"
  elif [ ! -f "$(dirname "$d")/.tool" ]; then
    report "'$(basename "$d")' not in a marked tool dir (no .tool in $(dirname "$d")): $d"
  fi
done < <(find _docs -type d \( -name specs -o -name plans \))

# C3 — *.md directly inside a marked tool dir's specs|plans must be dated
while IFS= read -r marker; do
  tooldir=${marker%/.tool}
  for sub in specs plans; do
    [ -d "$tooldir/$sub" ] || continue
    while IFS= read -r f; do
      base=$(basename "$f")
      [[ "$base" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-.+\.md$ ]] || report "undated file in $tooldir/$sub: $base (want YYYY-MM-DD-<name>.md)"
    done < <(find "$tooldir/$sub" -maxdepth 1 -type f -name '*.md')
  done
done < <(find _docs -type f -name .tool)

[ "$fail" -eq 0 ] && echo "docs-layout: OK"
exit "$fail"
