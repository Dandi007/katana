#!/usr/bin/env bash
# Mechanical hook inject test for retrieval:using-retrieval.
#
# The using-retrieval convention is injected via SessionStart hook (not Skill
# tool load), so skill_loaded never appears in agent trace. This test directly
# runs the hook script with KATANA_RETRIEVAL_SOURCES set and asserts its stdout
# contains the expected convention text.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# Without sources configured, hook must exit silently (zero output).
out_empty="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR KATANA_RETRIEVAL_SOURCES 2>/dev/null || true
    bash "$HOOK" 2>/dev/null
)"
case "$out_empty" in
    "") ok "hook silent when no sources configured" ;;
    *) bad "hook silent when no sources configured" "unexpected output: $out_empty" ;;
esac

# With sources configured, hook must inject using-retrieval convention.
out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_RETRIEVAL_SOURCES="search-note,web"
    bash "$HOOK" 2>/dev/null
)"

case "$out" in
    *hookSpecificOutput*) ok "hook emits hookSpecificOutput" ;;
    *) bad "hook emits hookSpecificOutput" "output: $out" ;;
esac

# Must inject retrieval routing convention keyword
case "$out" in
    *retrieval:route*|*using-retrieval*) ok "hook injects retrieval routing convention" ;;
    *) bad "hook injects retrieval routing convention" "output did not contain routing keyword" ;;
esac

# Sources placeholder must be replaced with actual value
case "$out" in
    *search-note*) ok "hook replaces SOURCES placeholder" ;;
    *) bad "hook replaces SOURCES placeholder" "output did not contain 'search-note'" ;;
esac

echo "-------------------------------------------"
echo "retrieval session-start inject: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
