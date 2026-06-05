# gitlab: glab/api 已知内部 project；缺 token 或 host 不通则 skip
HOST="$(katana_config_get gitlab_host "" "")"
TOK_ENV="$(katana_config_get gitlab_token_env "GITLAB_TOKEN_RO" "")"
TOK="$(eval echo "\${$TOK_ENV:-${GITLAB_TOKEN_RO:-}}")"
if [ -z "$HOST" ] || [ -z "$TOK" ]; then
  skip "gitlab" "no host/token"
else
  OUT="$(curl -s -m 20 --header "PRIVATE-TOKEN: $TOK" "https://$HOST/api/v4/version" 2>/dev/null)"
  assert_contains "gitlab" "$OUT" "version"
fi
