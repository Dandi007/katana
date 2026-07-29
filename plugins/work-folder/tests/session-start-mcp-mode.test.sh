#!/usr/bin/env bash
# SessionStart is unconditionally MCP-only and must never parse legacy path config.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/physical-kb-root-DO-NOT-LEAK"
LEGACY_ENV="$TMP/legacy-env-path-DO-NOT-LEAK"
mkdir -p "$KB" "$LEGACY_ENV"
KATANA_FILE="$TMP/.katana"
printf 'work_folder_interface=skill\nwork_folder_path=%s\n' "$TMP/config-path-DO-NOT-LEAK" > "$KATANA_FILE"

out="$(
    export HOME="$TMP/home"; mkdir -p "$HOME"
    export KATANA_KB_ROOT="$KB"
    export KATANA_CONFIG_FILE="$KATANA_FILE"
    export KATANA_WORK_FOLDER="$LEGACY_ENV"
    export KATANA_WORK_FOLDER_INTERFACE="skill"
    bash "$HOOK"
)"

if printf '%s' "$out" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    ok "mcp mode output parses as JSON"
else
    bad "mcp mode output parses as JSON" "output: $out"
fi

case "$out" in
    *wf_create*wf_search*wf_list*wf_resume*wf_save*) ok "names opaque-ID lifecycle tools" ;;
    *) bad "mcp mode names lifecycle tools" "output: $out" ;;
esac

case "$out" in
    *fs_read*fs_stat*fs_list*fs_create*fs_write*fs_edit*) ok "names ID-scoped MCP file tools" ;;
    *) bad "mcp mode names work-folder MCP file tools" "output: $out" ;;
esac

retired_file_discovery_tool="fs_"'glob'
case "$out" in
    *"$retired_file_discovery_tool"*)
        bad "does not advertise a retired file-discovery tool" "output: $out" ;;
    *) ok "does not advertise a retired file-discovery tool" ;;
esac

case "$out" in
    *folder_id*opaque*|*opaque*folder_id*) ok "explains opaque folder_id addressing" ;;
    *) bad "explains opaque folder_id addressing" "output: $out" ;;
esac

case "$out" in
    *"$KB"*|*"$LEGACY_ENV"*|*config-path-DO-NOT-LEAK*|*docs/work-records*|*工作记录/*)
        bad "hides physical and legacy logical paths" "output: $out" ;;
    *) ok "hides physical and legacy logical paths" ;;
esac

case "$out" in
    *原生文件工具*Git\ commit*) ok "pins MCP-only access and server Git persistence" ;;
    *) bad "pins MCP-only access and server Git persistence" "output: $out" ;;
esac

echo "-------------------------------------------"
echo "work-folder session-start mcp-mode: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
