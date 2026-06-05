# official-docs: 抓官方文档断言已知短语
PROXY="$(katana_config_get web_proxy "" "")"
OUT="$(curl -sL ${PROXY:+--proxy "$PROXY"} -m 40 "https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan" 2>/dev/null)"
assert_contains "official-docs" "$OUT" "Agent SDK"
