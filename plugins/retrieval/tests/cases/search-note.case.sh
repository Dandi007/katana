# search-note: migrated domains route to MCP; local fallback stays scope-locked.
SKILL="${CLAUDE_PLUGIN_ROOT}/skills/search-note/SKILL.md"
if grep -q 'wiki_search' "$SKILL" \
  && grep -q 'wf_search' "$SKILL" \
  && grep -q -- '--exclude-scope "智元工作/工作记录"' "$SKILL"; then
  pass "search-note"
else
  fail "search-note" "MCP routing or migrated-domain exclusion missing"
fi
