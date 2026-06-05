# github: gh api 公开 repo 断言字段；gh 未登录则 skip
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  skip "github" "gh not authed"
else
  OUT="$(gh api repos/anthropics/claude-code --jq '.stargazers_count' 2>/dev/null)"
  case "$OUT" in ''|*[!0-9]*) fail "github" "no stargazers_count: $OUT";; *) pass "github";; esac
fi
