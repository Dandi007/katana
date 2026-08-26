#!/usr/bin/env bash
# Test wiki_interface=mcp gates SessionStart to a short MCP trigger instead of
# the full using-wiki SKILL.md content; and that the default (no wiki_interface)
# keeps full skill injection (regression guard).
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

# ---------------------------------------------------------------------------
# Case A: wiki_interface=mcp via env var
# Expected: output contains short MCP trigger keywords (wiki_search, wiki_page_create)
# 2026-08-27 cutover：旧 katana-wiki-mcp 退役，工具面换成 wiki-v3 的 wiki_* op 名
#           and does NOT contain full SKILL.md-exclusive strings.
# ---------------------------------------------------------------------------
out_mcp="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WIKI_ROOT="."
    export KATANA_WIKI_INTERFACE="mcp"
    bash "$HOOK" 2>/dev/null
)"

case "$out_mcp" in
    *wiki_search*) ok "mcp mode: output contains wiki_search" ;;
    *) bad "mcp mode: output contains wiki_search" "output: $out_mcp" ;;
esac

case "$out_mcp" in
    *wiki_page_create*) ok "mcp mode: output contains wiki_page_create" ;;
    *) bad "mcp mode: output contains wiki_page_create" "output: $out_mcp" ;;
esac

# The full SKILL.md has "using-wiki" in its title/frontmatter; short trigger must not.
case "$out_mcp" in
    *"using-wiki"*) bad "mcp mode: output must NOT contain full using-wiki content" "found 'using-wiki' in output" ;;
    *) ok "mcp mode: output does not contain full using-wiki content" ;;
esac

# Must still be valid JSON with hookSpecificOutput
case "$out_mcp" in
    *hookSpecificOutput*) ok "mcp mode: output is hookSpecificOutput JSON" ;;
    *) bad "mcp mode: output is hookSpecificOutput JSON" "output: $out_mcp" ;;
esac

if printf '%s' "$out_mcp" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    ok "mcp mode: output parses as JSON"
else
    bad "mcp mode: output parses as JSON" "output: $out_mcp"
fi

case "$out_mcp" in
    *read/grep*|*Read/Grep*|*"$KB"*) bad "mcp mode: no native-fs guidance or physical root" "output: $out_mcp" ;;
    *) ok "mcp mode: no native-fs guidance or physical root" ;;
esac

case "$out_mcp" in
    *wiki_page_get*) ok "mcp mode: output names wiki MCP deep-read tools" ;;
    *) bad "mcp mode: output names wiki MCP deep-read tools" "output: $out_mcp" ;;
esac

# ---------------------------------------------------------------------------
# Case A2: wiki_interface=mcp via .katana config file
# ---------------------------------------------------------------------------
KATANA_FILE="$TMP/dot-katana-mcp"
printf 'wiki_interface=mcp\n' > "$KATANA_FILE"
rm -f "$KB/WIKI.md"

out_mcp2="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR KATANA_WIKI_INTERFACE 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WIKI_ROOT="."
    export KATANA_CONFIG_FILE="$KATANA_FILE"
    bash "$HOOK" 2>/dev/null
)"

case "$out_mcp2" in
    *wiki_search*) ok "mcp mode via .katana: output contains wiki_search" ;;
    *) bad "mcp mode via .katana: output contains wiki_search" "output: $out_mcp2" ;;
esac

case "$out_mcp2" in
    *hookSpecificOutput*) ok "mcp mode via .katana: injects without client WIKI.md mount" ;;
    *) bad "mcp mode via .katana: injects without client WIKI.md mount" "output: $out_mcp2" ;;
esac

case "$out_mcp2" in
    *read/grep*|*Read/Grep*|*"$KB"*) bad "mcp mode via .katana: no native-fs guidance or physical root" "output: $out_mcp2" ;;
    *) ok "mcp mode via .katana: no native-fs guidance or physical root" ;;
esac

# ---------------------------------------------------------------------------
# Case B: default (no wiki_interface) → full skill injection (regression guard)
# ---------------------------------------------------------------------------
: > "$KB/WIKI.md"
out_skill="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR KATANA_WIKI_INTERFACE 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_WIKI_ROOT="."
    bash "$HOOK" 2>/dev/null
)"

case "$out_skill" in
    *using-wiki*|*wiki:query*) ok "skill default: output contains using-wiki convention" ;;
    *) bad "skill default: output contains using-wiki convention" "output did not contain wiki convention keyword" ;;
esac

case "$out_skill" in
    *hookSpecificOutput*) ok "skill default: output is hookSpecificOutput JSON" ;;
    *) bad "skill default: output is hookSpecificOutput JSON" "output: $out_skill" ;;
esac

# Default must NOT be the short MCP trigger — should contain full SKILL.md content
# (the short trigger does not contain {{WIKI_ROOT}} expansion or SKILL.md title).
# We verify by checking the WIKI_ROOT absolute path appears (placeholder replaced).
case "$out_skill" in
    *"$KB"*) ok "skill default: WIKI_ROOT placeholder expanded (full SKILL.md)" ;;
    *) bad "skill default: WIKI_ROOT placeholder expanded (full SKILL.md)" "kb path [$KB] not found in output" ;;
esac

echo "-------------------------------------------"
echo "wiki session-start mcp-mode: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
