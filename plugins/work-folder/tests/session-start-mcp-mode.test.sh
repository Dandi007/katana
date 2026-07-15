#!/usr/bin/env bash
# MCP-mode injection must be valid, tool-only guidance with no server mount path.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KATANA_FILE="$TMP/.katana"
printf 'work_folder_interface=mcp\nwork_folder_path=/server-only/work-records\n' > "$KATANA_FILE"

out="$(
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset CLAUDE_PROJECT_DIR KATANA_WORK_FOLDER_INTERFACE KATANA_WORK_FOLDER 2>/dev/null || true
    export KATANA_CONFIG_FILE="$KATANA_FILE"
    bash "$HOOK" 2>/dev/null
)"

if printf '%s' "$out" | python3 -m json.tool >/dev/null 2>&1; then
    ok "mcp mode output parses as JSON"
else
    bad "mcp mode output parses as JSON" "output: $out"
fi

case "$out" in
    *wf_create*wf_resume*wf_save*wf_search*fs_read*fs_glob*fs_list*) ok "mcp mode names work-folder MCP tools" ;;
    *) bad "mcp mode names work-folder MCP tools" "output: $out" ;;
esac

case "$out" in
    *"read/grep"*|*"Read/Grep"*|*"自行 read"*|*"自行 grep"*) bad "mcp mode has no native read/grep guidance" "output: $out" ;;
    *) ok "mcp mode has no native read/grep guidance" ;;
esac

case "$out" in
    *"/server-only/work-records"*|*"work folder 根"*) bad "mcp mode hides physical work-folder root" "output: $out" ;;
    *) ok "mcp mode hides physical work-folder root" ;;
esac

echo "-------------------------------------------"
echo "work-folder session-start mcp-mode: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
