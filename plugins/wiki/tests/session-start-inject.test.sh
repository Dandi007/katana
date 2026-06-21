#!/usr/bin/env bash
# Mechanical hook inject test for wiki:using-wiki.
#
# The using-wiki convention is injected via SessionStart hook (not Skill tool
# load), so skill_loaded never appears in agent trace. This test directly runs
# the hook script with KATANA_WIKI_ROOT pointing to a temp KB containing
# WIKI.md, and asserts stdout contains the expected convention text.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"
mkdir -p "$KB"
: > "$KB/WIKI.md"

# Without wiki_root configured, hook must exit silently.
out_empty="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR KATANA_WIKI_ROOT 2>/dev/null || true
    bash "$HOOK" 2>/dev/null
)"
case "$out_empty" in
    "") ok "hook silent when no wiki_root configured" ;;
    *) bad "hook silent when no wiki_root configured" "unexpected output: $out_empty" ;;
esac

# With wiki_root configured and WIKI.md present, hook must inject convention.
out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WIKI_ROOT="."
    bash "$HOOK" 2>/dev/null
)"

case "$out" in
    *hookSpecificOutput*) ok "hook emits hookSpecificOutput" ;;
    *) bad "hook emits hookSpecificOutput" "output: $out" ;;
esac

# Must inject the using-wiki convention keyword
case "$out" in
    *using-wiki*|*wiki:query*) ok "hook injects using-wiki convention" ;;
    *) bad "hook injects using-wiki convention" "output did not contain wiki convention keyword" ;;
esac

# WIKI_ROOT placeholder must be replaced with absolute path
case "$out" in
    *"$KB"*) ok "hook replaces WIKI_ROOT placeholder with absolute path" ;;
    *) bad "hook replaces WIKI_ROOT placeholder with absolute path" "kb path [$KB] not found in output" ;;
esac

echo "-------------------------------------------"
echo "wiki session-start inject: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
