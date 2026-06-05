# search-note: 本地 KB grep 已知笔记关键词（只读）；KB 不可达则 skip
KB="$(katana_config_get kb_dir "" "")"
case "$KB" in
  "."|"") KB="${CLAUDE_PROJECT_DIR:-$(pwd)}";;
  /*) :;;
  *) KB="${CLAUDE_PROJECT_DIR:-$(pwd)}/$KB";;
esac
if [ -d "$KB/Zettelkasten" ]; then
  HITS="$(grep -rl "Agent SDK credit" "$KB/Zettelkasten" 2>/dev/null | wc -l | tr -d ' ')"
  [ "$HITS" -ge 1 ] && pass "search-note" || fail "search-note" "0 hits for known note"
else
  skip "search-note" "kb unavailable"
fi
