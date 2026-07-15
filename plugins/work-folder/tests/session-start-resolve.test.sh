#!/usr/bin/env bash
# Zero-mount test for the work-folder SessionStart hook. A configured physical
# path must not enter the injected client guidance.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"; OTHER="$TMP/other"
mkdir -p "$KB" "$OTHER"

out="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    cd "$OTHER" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WORK_FOLDER="智元工作/工作记录"
    bash "$HOOK"
)"

case "$out" in
    *"$KB"*|*"智元工作/工作记录"*) bad "no work-folder path leaks" "configured path leaked into output" ;;
    *) ok "no work-folder path leaks" ;;
esac

# Must NOT leak the cwd (OTHER).
case "$out" in
    *"$OTHER"*) bad "no cwd leak" "cwd [$OTHER] leaked into output" ;;
    *) ok "no cwd leak" ;;
esac

case "$out" in
    *wf_create*wf_resume*wf_search*fs_read*fs_write*fs_edit*wf_save*) ok "skill guidance routes storage through MCP" ;;
    *) bad "skill guidance routes storage through MCP" "output: $out" ;;
esac

echo "-------------------------------------------"
echo "work-folder session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
