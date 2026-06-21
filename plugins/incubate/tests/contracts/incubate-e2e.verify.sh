#!/usr/bin/env bash
# 多轮 incubate e2e 的末轮校验。CWD 由 harness 注入（case 的 kb cwd）。
set -uo pipefail
root="$CWD/Incubator"
dir="$(find "$root" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | head -1)"
[ -n "$dir" ] || { echo "FAIL: no Incubator/YYYY/MM/<topic> dir under $root"; exit 1; }
fail=0
test -f "$dir/README.md"        || { echo "FAIL: missing README.md"; fail=1; }
grep -q "已毕业" "$dir/README.md" 2>/dev/null || { echo "FAIL: README 状态非已毕业"; fail=1; }
grep -qE "实时|采集|完整性" "$dir/golden-order.md" 2>/dev/null || { echo "FAIL: golden-order 未捕获想法"; fail=1; }
grep -qE "来源|http|\[\[|task|Task" "$dir/findings.md" 2>/dev/null || { echo "FAIL: findings 无 gather 素材"; fail=1; }
test -s "$dir/spec.md"          || { echo "FAIL: spec.md 空（未 synthesize）"; fail=1; }
[ "$fail" = 0 ] && echo "OK: $dir" || exit 1
