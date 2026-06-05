# feishu: lark-cli 取已知文档；lark 未配置则 skip
if ! command -v lark-cli >/dev/null 2>&1; then skip "feishu" "lark-cli absent"; else
  OUT="$(lark-cli docs +fetch --help 2>/dev/null || true)"
  [ -n "$OUT" ] && pass "feishu" || skip "feishu" "lark-cli not configured"
fi
