#!/usr/bin/env bash
# Resolve test for the feishu-docs SessionStart hook.
#
# A relative feishu_docs_root must resolve against kb-root (not cwd). We create
# <kb>/<rel> so the hook activates (dir must exist), run from a non-KB cwd, and
# assert the injected content references the absolute "<kb>/<rel>" path.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"; OTHER="$TMP/other"
REL="docs/feishu"
mkdir -p "$KB/$REL" "$OTHER"

out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    cd "$OTHER" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_FEISHU_DOCS_ROOT="$REL"
    bash "$HOOK"
)"

case "$out" in
    *"$KB/$REL"*) ok "feishu_docs_root resolved against kb-root" ;;
    *) bad "feishu_docs_root resolved against kb-root" "missing [$KB/$REL]: $out" ;;
esac

case "$out" in
    *"$OTHER"*) bad "no cwd leak" "cwd [$OTHER] leaked" ;;
    *) ok "no cwd leak" ;;
esac

echo "-------------------------------------------"
echo "feishu-docs session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
