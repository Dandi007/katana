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

if printf '%s' "$out_mcp" | python3 -m json.tool >/dev/null 2>&1; then
    ok "mcp mode: output parses as JSON"
else
    bad "mcp mode: output parses as JSON" "output: $out_mcp"
fi

case "$out_mcp" in
    *fs_read*fs_glob*) ok "mcp mode: deep reads use wiki MCP fs tools" ;;
    *) bad "mcp mode: deep reads use wiki MCP fs tools" "output: $out_mcp" ;;
esac

case "$out_mcp" in
    *"read/grep"*|*"Read/Grep"*|*"自行 read"*|*"自行 grep"*) bad "mcp mode: no native read/grep guidance" "output: $out_mcp" ;;
    *) ok "mcp mode: no native read/grep guidance" ;;
esac

case "$out_mcp" in
    *"$KB"*) bad "mcp mode: no physical wiki root exposure" "leaked [$KB]" ;;
    *) ok "mcp mode: no physical wiki root exposure" ;;
esac

# ---------------------------------------------------------------------------
# Case A2: wiki_interface=mcp via .katana config file
# ---------------------------------------------------------------------------
KATANA_FILE="$TMP/dot-katana-mcp"
printf 'wiki_interface=mcp\nwiki_root=/server-only/wiki\n' > "$KATANA_FILE"

out_mcp2="$(
    set -uo pipefail
    export HOME="$TMP/home"; mkdir -p "$HOME"
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR KATANA_WIKI_INTERFACE 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    export KATANA_CONFIG_FILE="$KATANA_FILE"
    bash "$HOOK" 2>/dev/null
)"

case "$out_mcp2" in
    *wiki_query*) ok "mcp mode via .katana: output contains wiki_query" ;;
    *) bad "mcp mode via .katana: output contains wiki_query" "output: $out_mcp2" ;;
esac

case "$out_mcp2" in
    *"/server-only/wiki"*) bad "mcp mode via .katana: no server root exposure" "output: $out_mcp2" ;;
    *) ok "mcp mode via .katana: no server root exposure" ;;
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

# Default still injects the full convention skill, but zero-mount guidance must
# not expose the mounted physical root.
case "$out_skill" in
    *"$KB"*) bad "skill default: physical wiki root hidden" "kb path [$KB] leaked in output" ;;
    *) ok "skill default: physical wiki root hidden" ;;
esac

case "$out_skill" in
    *wiki_search*fs_read*wiki_ingest_plan*wiki_ingest_apply*) ok "skill default: full convention uses MCP tools" ;;
    *) bad "skill default: full convention uses MCP tools" "output: $out_skill" ;;
esac

echo "-------------------------------------------"
echo "wiki session-start mcp-mode: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
