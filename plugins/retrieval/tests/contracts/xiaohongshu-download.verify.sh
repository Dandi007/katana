#!/usr/bin/env bash
# 继承原 agent-e2e 的笔记级断言（路径在 glob 后才可知，原语表达不了链式取值）
set -uo pipefail
DIR=$(find "$KB_DIR" -maxdepth 1 -type d -name "小红书-*" | head -1)
[ -n "$DIR" ] || { echo "no dir"; exit 1; }
grep -q '|' "$DIR/index.md" || { echo "index.md has no table rows"; exit 1; }
NOTE=$(find "$DIR" -name "*.md" ! -name index.md | head -1)
[ -n "$NOTE" ] || { echo "no note"; exit 1; }
grep -q "xsec_token" "$NOTE" || { echo "no xsec_token"; exit 1; }
for k in author likes note_id fetched_at; do
  grep -q "^$k:" "$NOTE" || { echo "no $k"; exit 1; }
done
[ "$(wc -c < "$NOTE")" -gt 500 ] || { echo "too small"; exit 1; }
