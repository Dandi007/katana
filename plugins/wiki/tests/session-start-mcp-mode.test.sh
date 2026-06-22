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
# Expected: output contains short MCP trigger keywords (wiki_query, wiki_ingest)
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
    *wiki_query*) ok "mcp mode: output contains wiki_query" ;;
    *) bad "mcp mode: output contains wiki_query" "output: $out_mcp" ;;
esac

case "$out_mcp" in
    *wiki_ingest*) ok "mcp mode: output contains wiki_ingest" ;;
    *) bad "mcp mode: output contains wiki_ingest" "output: $out_mcp" ;;
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

# ---------------------------------------------------------------------------
# Case A2: wiki_interface=mcp via .katana config file
# ---------------------------------------------------------------------------
KATANA_FILE="$TMP/dot-katana-mcp"
printf 'wiki_interface=mcp\n' > "$KATANA_FILE"

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
    *wiki_query*) ok "mcp mode via .katana: output contains wiki_query" ;;
    *) bad "mcp mode via .katana: output contains wiki_query" "output: $out_mcp2" ;;
esac

# ---------------------------------------------------------------------------
# Case B: default (no wiki_interface) → full skill injection (regression guard)
# ---------------------------------------------------------------------------
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
