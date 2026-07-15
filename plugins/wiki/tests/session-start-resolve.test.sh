#!/usr/bin/env bash
# Zero-mount test for the wiki SessionStart hook. A relative wiki_root still
# activates the legacy convention when WIKI.md is mounted, but its resolved
# physical path must not enter client guidance.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"; OTHER="$TMP/other"
mkdir -p "$KB" "$OTHER"
# Activation condition: wiki_root="." -> WIKI.md lives at KB root.
: > "$KB/WIKI.md"

out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    cd "$OTHER" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WIKI_ROOT="."
    bash "$HOOK"
)"

case "$out" in
    *"$KB"*) bad "wiki root is not exposed" "kb path [$KB] leaked into output: $out" ;;
    *) ok "wiki root is not exposed" ;;
esac

case "$out" in
    *"$OTHER"*) bad "no cwd leak" "cwd [$OTHER] leaked into output" ;;
    *) ok "no cwd leak" ;;
esac

case "$out" in
    *wiki_query*wiki_search*fs_read*fs_glob*wiki_ingest_plan*wiki_ingest_apply*) ok "legacy convention routes wiki through MCP" ;;
    *) bad "legacy convention routes wiki through MCP" "output: $out" ;;
esac

echo "-------------------------------------------"
echo "wiki session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
