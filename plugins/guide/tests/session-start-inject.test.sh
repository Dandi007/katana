#!/usr/bin/env bash
# Mechanical hook inject test for guide:using-katana.
#
# The using-katana convention is injected via SessionStart hook (not Skill tool
# load), so skill_loaded never appears in agent trace. This test directly runs
# the hook script and asserts its stdout contains the expected convention text.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

# The guide hook always injects using-katana content (no config gate).
out="$(bash "$HOOK" 2>/dev/null)"

# Must emit hookSpecificOutput JSON
case "$out" in
    *hookSpecificOutput*) ok "hook emits hookSpecificOutput" ;;
    *) bad "hook emits hookSpecificOutput" "output: $out" ;;
esac

# Must inject the using-katana convention keyword
case "$out" in
    *using-katana*) ok "hook injects using-katana keyword" ;;
    *) bad "hook injects using-katana keyword" "output did not contain 'using-katana'" ;;
esac

# Must mention work folder (core concept of the katana convention)
case "$out" in
    *work-folder*|*"work folder"*) ok "hook mentions work folder concept" ;;
    *) bad "hook mentions work folder concept" "output did not mention work folder" ;;
esac

echo "-------------------------------------------"
echo "guide session-start inject: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
