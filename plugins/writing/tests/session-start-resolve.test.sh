#!/usr/bin/env bash
# Resolve test for the writing SessionStart hook.
#
# A relative writing_dir (default .katana-writing) must resolve against kb-root
# (not cwd). We create <kb>/<rel> so the hook activates (dir must exist), run
# from a non-KB cwd, and assert {{WRITING_DIR}} is replaced with the absolute
# "<kb>/<rel>" path, never the cwd.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"; OTHER="$TMP/other"
REL=".katana-writing"
mkdir -p "$KB/$REL" "$OTHER"

out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    cd "$OTHER" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WRITING_DIR="$REL"
    bash "$HOOK"
)"

case "$out" in
    *"$KB/$REL"*) ok "writing_dir resolved against kb-root" ;;
    *) bad "writing_dir resolved against kb-root" "missing [$KB/$REL]: $out" ;;
esac

case "$out" in
    *"$OTHER"*) bad "no cwd leak" "cwd [$OTHER] leaked" ;;
    *) ok "no cwd leak" ;;
esac

echo "-------------------------------------------"
echo "writing session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
