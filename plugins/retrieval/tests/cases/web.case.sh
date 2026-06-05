# web: 抓稳定页断言已知串；主路 WebFetch 由 agent 执行，脚本侧用 curl 等价验证连通
PROXY="$(katana_config_get web_proxy "" "")"
OUT="$(curl -s ${PROXY:+--proxy "$PROXY"} -m 30 https://example.com/ 2>/dev/null)"
assert_contains "web" "$OUT" "Example Domain"
