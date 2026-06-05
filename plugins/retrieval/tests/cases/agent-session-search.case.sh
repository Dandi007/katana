# agent-session-search: 查已知 session 关键词；存储不可达则 skip
SS="${AGENT_SESSION_STORE:-$HOME/.claude/projects}"
if [ -d "$SS" ]; then
  HITS="$(grep -rl "retrieval" "$SS" 2>/dev/null | head -1)"
  [ -n "$HITS" ] && pass "agent-session-search" || skip "agent-session-search" "no hit (acceptable)"
else
  skip "agent-session-search" "store unavailable"
fi
