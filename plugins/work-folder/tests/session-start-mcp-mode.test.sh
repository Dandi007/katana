#!/usr/bin/env bash
# MCP mode must inject only logical MCP guidance and never expose the client KB root.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/physical-kb-root"
mkdir -p "$KB"
KATANA_FILE="$TMP/.katana"
printf 'work_folder_interface=mcp\nwork_folder_path=智元工作/工作记录\n' > "$KATANA_FILE"

out="$(
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset CLAUDE_PROJECT_DIR KATANA_WORK_FOLDER KATANA_WORK_FOLDER_INTERFACE 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_CONFIG_FILE="$KATANA_FILE"
    bash "$HOOK"
)"

if printf '%s' "$out" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    ok "mcp mode output parses as JSON"
else
    bad "mcp mode output parses as JSON" "output: $out"
fi

case "$out" in
    *wf_create*wf_resume*wf_save*wf_search*) ok "mcp mode names lifecycle tools" ;;
    *) bad "mcp mode names lifecycle tools" "output: $out" ;;
esac

case "$out" in
    *fs_read*fs_glob*fs_list*) ok "mcp mode names work-folder MCP file tools" ;;
    *) bad "mcp mode names work-folder MCP file tools" "output: $out" ;;
esac

case "$out" in
    *read/grep*|*Read/Grep*|*"$KB"*) bad "mcp mode hides native-fs guidance and physical root" "output: $out" ;;
    *) ok "mcp mode hides native-fs guidance and physical root" ;;
esac

echo "-------------------------------------------"
echo "work-folder session-start mcp-mode: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
