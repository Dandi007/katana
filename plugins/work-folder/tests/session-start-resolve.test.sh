#!/usr/bin/env bash
# Resolve test for the work-folder SessionStart hook.
#
# With KATANA_KB_ROOT set to a temp KB and a *relative* work_folder_path
# (via KATANA_WORK_FOLDER), running the hook from a cwd that is NOT the KB
# must inject the ABSOLUTE "<kb>/<rel>" path into additionalContext — never
# the bare relative value, and never a cwd-derived path.
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
    *"$KB/智元工作/工作记录"*) ok "injects absolute work_folder_path under kb-root" ;;
    *) bad "injects absolute work_folder_path under kb-root" "missing [$KB/智元工作/工作记录] in output" ;;
esac

# Must NOT leak the bare relative value as a standalone path token.
case "$out" in
    *"\`智元工作/工作记录/YYYY"*)
        bad "no bare relative value leaks" "found bare relative path in template output" ;;
    *) ok "no bare relative value leaks" ;;
esac

# Must NOT leak the cwd (OTHER).
case "$out" in
    *"$OTHER"*) bad "no cwd leak" "cwd [$OTHER] leaked into output" ;;
    *) ok "no cwd leak" ;;
esac

echo "-------------------------------------------"
echo "work-folder session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
