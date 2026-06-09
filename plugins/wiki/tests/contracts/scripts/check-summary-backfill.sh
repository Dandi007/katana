#!/usr/bin/env bash
# 断言：lint 跑完后，笔记/ 下每页 frontmatter 都有非空 摘要，且正文 byte 不变。
# 由 contract 的 script 断言调用，env: KB_DIR（lint 跑过的库副本）。
set -uo pipefail
GOLDEN="$KB_DIR/.golden"
fail=0
for f in 手冲咖啡萃取 咖啡豆烘焙度 V60滤杯; do
  page="$KB_DIR/笔记/$f.md"
  [ -f "$page" ] || { echo "MISSING: $page"; fail=1; continue; }
  summ="$(awk 'c==1 && /^摘要:[[:space:]]*[^[:space:]]/{print; exit} /^---$/{c++}' "$page")"
  [ -n "$summ" ] || { echo "NO-SUMMARY: $f"; fail=1; }
  body="$(awk 'c>=2{print} /^---$/{c++}' "$page")"
  if ! diff -q <(printf '%s\n' "$body") "$GOLDEN/$f.body" >/dev/null 2>&1; then
    echo "BODY-CHANGED: $f"; fail=1
  fi
done
exit $fail
