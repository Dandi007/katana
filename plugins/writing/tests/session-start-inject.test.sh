#!/usr/bin/env bash
# Mechanical hook inject test for writing:using-writing.
#
# The using-writing convention is injected via SessionStart hook (not Skill
# tool load), so skill_loaded never appears in agent trace. This test directly
# runs the hook script with KATANA_WRITING_DIR pointing to an existing temp
# directory, and asserts stdout contains the expected convention text.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"
REL=".katana-writing"
mkdir -p "$KB/$REL"

# Without writing_dir configured, hook must exit silently.
out_empty="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR KATANA_WRITING_DIR 2>/dev/null || true
    bash "$HOOK" 2>/dev/null
)"
case "$out_empty" in
    "") ok "hook silent when no writing_dir configured" ;;
    *) bad "hook silent when no writing_dir configured" "unexpected output: $out_empty" ;;
esac

# With writing_dir configured and directory present, hook must inject convention.
out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WRITING_DIR="$REL"
    bash "$HOOK" 2>/dev/null
)"

case "$out" in
    *hookSpecificOutput*) ok "hook emits hookSpecificOutput" ;;
    *) bad "hook emits hookSpecificOutput" "output: $out" ;;
esac

# Must inject the using-writing convention keyword
case "$out" in
    *using-writing*|*writing:write*) ok "hook injects using-writing convention" ;;
    *) bad "hook injects using-writing convention" "output did not contain writing convention keyword" ;;
esac

# WRITING_DIR placeholder must be replaced with absolute path
case "$out" in
    *"$KB/$REL"*) ok "hook replaces WRITING_DIR placeholder with absolute path" ;;
    *) bad "hook replaces WRITING_DIR placeholder with absolute path" "path [$KB/$REL] not found in output" ;;
esac

echo "-------------------------------------------"
echo "writing session-start inject: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
