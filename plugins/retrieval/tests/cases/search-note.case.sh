# search-note: 本地 KB grep 已知笔记关键词（只读）；KB 不可达则 skip
# kb_dir 经 katana_resolve_path 解析（基准 katana_kb_root，非 cwd），与 skill 一致。
KB="$(katana_resolve_path "$(katana_config_get kb_dir "." "")")"
if [ -d "$KB/Zettelkasten" ]; then
  HITS="$(grep -rl "Agent SDK credit" "$KB/Zettelkasten" 2>/dev/null | wc -l | tr -d ' ')"
  [ "$HITS" -ge 1 ] && pass "search-note" || fail "search-note" "0 hits for known note"
else
  skip "search-note" "kb unavailable"
fi
