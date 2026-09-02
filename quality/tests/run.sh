#!/bin/bash
# Every guard suite quality.json names, in one go -- roughly two minutes, not
# seconds. The list comes from check-guard-suites.py --list (the swept
# suites with not_suites dropped and exempt ones kept -- they are suites,
# and running them is the point) when quality.json has a guard_suites
# section, else every test-* beside this script; run from the repository
# root by extension.
# Exit is the count that failed. Run from anywhere; the list is repo-relative
# and check-guard-suites.py finds the repository itself.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
cd "$repo"

# Register as running, so attach.py --refresh will not replace quality/ underneath this.
mkdir -p "$here/../.running"; echo "tests/run.sh" > "$here/../.running/$$"
trap 'rm -f "$here/../.running/$$"' EXIT

suites=()
if python3 -c 'import json,sys; sys.exit(0 if "guard_suites" in json.load(open("quality.json")) else 1)' 2>/dev/null; then
  while IFS= read -r suite; do
    suites+=("$suite")
  done < <(python3 quality/bin/check-guard-suites.py --list)
else
  # no guard_suites section: every suite beside this script, in name order
  while IFS= read -r suite; do
    suites+=("$suite")
  done < <(cd "$repo" && ls quality/tests/test-*.py quality/tests/test-*.sh 2>/dev/null | sort)
fi
if [ "${#suites[@]}" = 0 ]; then
  echo "quality/tests/run.sh: no suites found" >&2
  exit 1
fi

if [ "${1:-}" = "--dry-run" ]; then
  printf '%s\n' "${suites[@]}"
  exit 0
fi

failed=0
for suite in "${suites[@]}"; do
  printf '== %s\n' "$suite"
  case "$suite" in
    *.py) python3 "$suite" ;;
    *.sh) bash "$suite" ;;
  esac || { failed=$((failed + 1)); printf '   FAILED: %s\n' "$suite"; }
done

printf '\nquality/tests/run.sh: %s\n' "$([ "$failed" = 0 ] && echo 'all passed.' || echo "$failed failed.")"
exit "$failed"
