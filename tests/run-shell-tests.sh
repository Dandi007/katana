#!/usr/bin/env bash
# tests/run-shell-tests.sh — discover and run every plugin + top-level shell
# unit test (*.test.sh), aggregate pass/fail, exit non-zero if any fails.
#
# Discovery: plugins/*/tests/*.test.sh and tests/*.test.sh. Each test is run in
# its own `bash` process (tests self-isolate via mktemp, so run order does not
# matter — re-running is idempotent). A clean output line per test plus a final
# summary makes CI failures easy to read.
#
# bash 3.2 compatible (no globstar / mapfile); C-locale safe (find -print0 with
# LC_ALL=C sort so byte order is stable regardless of locale).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

# Collect test files (NUL-delimited, byte-sorted) without a pipe subshell that
# would lose the array under bash 3.2.
tests=()
while IFS= read -r -d '' f; do
    tests+=("$f")
done < <(
    {
        find "$REPO/plugins" -path '*/tests/*.test.sh' -print0 2>/dev/null
        find "$HERE" -maxdepth 1 -name '*.test.sh' -print0 2>/dev/null
    } | LC_ALL=C sort -z
)

if [ "${#tests[@]}" -eq 0 ]; then
    echo "run-shell-tests: no *.test.sh found" >&2
    exit 1
fi

total=0; passed=0; failed=0
failed_names=""

for t in ${tests[@]+"${tests[@]}"}; do
    total=$((total + 1))
    rel="${t#"$REPO"/}"
    if bash "$t" >/tmp/run-shell-tests.$$.log 2>&1; then
        passed=$((passed + 1))
        printf 'PASS  %s\n' "$rel"
    else
        failed=$((failed + 1))
        failed_names="${failed_names}\n  - ${rel}"
        printf 'FAIL  %s\n' "$rel"
        # Surface the failing test's own output (indented) for quick triage.
        sed 's/^/      /' /tmp/run-shell-tests.$$.log
    fi
done
rm -f /tmp/run-shell-tests.$$.log

echo "==========================================="
printf 'shell tests: %d total, %d passed, %d failed\n' "$total" "$passed" "$failed"
if [ "$failed" -ne 0 ]; then
    printf 'failing:%b\n' "$failed_names" >&2
    exit 1
fi
