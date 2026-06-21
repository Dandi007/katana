#!/usr/bin/env bash
# Resolve test for the wiki SessionStart hook.
#
# A relative wiki_root must resolve against kb-root (not cwd). We create a
# temp KB with WIKI.md so the hook activates, set KATANA_WIKI_ROOT to a
# relative value, run from a non-KB cwd, and assert {{WIKI_ROOT}} is replaced
# with "<kb>/<rel>" (absolute), never the cwd.
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

# "." resolves to "<kb>/." -> the injected WIKI_ROOT contains the kb prefix.
case "$out" in
    *"$KB"*) ok "wiki_root resolved against kb-root" ;;
    *) bad "wiki_root resolved against kb-root" "missing kb prefix [$KB] in output: $out" ;;
esac

case "$out" in
    *"$OTHER"*) bad "no cwd leak" "cwd [$OTHER] leaked into output" ;;
    *) ok "no cwd leak" ;;
esac

echo "-------------------------------------------"
echo "wiki session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
