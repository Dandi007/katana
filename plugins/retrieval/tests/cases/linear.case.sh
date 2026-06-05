# linear: GraphQL viewer 查询；缺 key 则 skip
TOK_ENV="$(katana_config_get linear_token_env "LINEAR_API_KEY" "")"
TOK="$(eval echo "\${$TOK_ENV:-}")"
if [ -z "$TOK" ]; then
  skip "linear" "no api key"
else
  OUT="$(curl -s -m 20 -X POST https://api.linear.app/graphql -H "Authorization: $TOK" -H "Content-Type: application/json" -d '{"query":"{ viewer { id name } }"}' 2>/dev/null)"
  assert_contains "linear" "$OUT" "viewer"
fi
