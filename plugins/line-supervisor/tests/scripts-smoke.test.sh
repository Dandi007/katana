#!/usr/bin/env bash
# scripts-smoke.test.sh — 不依赖生产面的机械检查：脚本可解析、用法守卫、只读汇总在空 root 上能跑。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../skills/supervise/scripts"
fail=0
bash -n "$S/readings.sh" || { echo "FAIL: readings.sh syntax"; fail=1; }
python3 -m py_compile "$S/monitor.py" "$S/opencode-tokens.py" || { echo "FAIL: py_compile"; fail=1; }
# usage guards
bash "$S/readings.sh" >/dev/null 2>&1 && { echo "FAIL: readings.sh without line_id should exit non-zero"; fail=1; }
python3 "$S/monitor.py" >/dev/null 2>&1 && { echo "FAIL: monitor.py without line_id should exit non-zero"; fail=1; }
# opencode-tokens on an empty root: no dbs, no crash
tmp="$(mktemp -d)"; mkdir -p "$tmp/dd" "$tmp/runs"
out="$(python3 "$S/opencode-tokens.py" 1 "$tmp" 2>&1)"; rm -rf "$tmp"
echo "$out" | grep -q "'dbs': 0" || { echo "FAIL: opencode-tokens on empty root: $out"; fail=1; }
# SKILL.md frontmatter + references present
for f in SKILL.md references/checklist.md references/incident-ladder.md references/progress-format.md; do
  test -f "$HERE/../skills/supervise/$f" || { echo "FAIL: missing $f"; fail=1; }
done
head -1 "$HERE/../skills/supervise/SKILL.md" | grep -q '^---$' || { echo "FAIL: SKILL.md frontmatter"; fail=1; }
[ "$fail" = 0 ] && echo "OK: line-supervisor scripts smoke" || exit 1
