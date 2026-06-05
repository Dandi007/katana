# twitter: 登录态在 chrome profile 才可跑；CI/无 profile 则 skip
PROF="$(katana_config_get twitter_chrome_profile "" "")"
case "$PROF" in "~"*) PROF="${HOME}${PROF#\~}";; esac
if [ -z "$PROF" ] || [ ! -d "$PROF" ]; then
  skip "twitter" "no chrome profile (login session absent)"
else
  pass "twitter"  # profile 存在即视为可用；真实读推由 agent 经 Playwright MCP 执行
fi
