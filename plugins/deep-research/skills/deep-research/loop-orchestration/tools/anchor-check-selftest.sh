#!/usr/bin/env bash
# anchor-check-selftest.sh — N2b 硬验收（E1–E9）自检入口（E9：本路径必须存在且 exit 0）
#
# 判据：校验器必须以**生产者的实际格式**为准：
#   code://<path>@<sha>#L<a>[-L<b>]  （locator 为仓内相对路径，repo 归属由 --repo-root 外部提供）
# 三类锚点显式分类、分别计数、总和 === 输入条数；缺失 repo-root / fetcher 取不到 / 空内容 → 响亮失败。
#
# E1..E8 在此求值；E9 即本脚本自身（存在且 exit 0）；E10（不碰 .dd-evidence/）由提交边界保证。
set -u

# ── 路径发现 ──
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/anchor-check.py"
REPO_ROOT_OVERRIDE="${ANCHOR_REPO_ROOT:-/data/code/self/loop-engine-deep-research-plugin}"

PY=python3
[ -x "$(command -v $PY)" ] || { echo "E9-FAIL: python3 not found"; exit 9; }
[ -f "$TOOL" ] || { echo "E9-FAIL: $TOOL not found"; exit 9; }

pass=0; fail=0
ok(){ echo "  PASS $1"; pass=$((pass+1)); }
bad(){ echo "  FAIL $1"; fail=$((fail+1)); }

run_tool(){ # $1.. -> writes json to $OUT, exit code to $RC
  OUT="$(mktemp)"
  "$PY" "$TOOL" "$@" >"$OUT" 2>&1
  RC=$?
}
field(){ # $1=file $2=key
  "$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get(sys.argv[2]))" "$1" "$2"
}

echo "== E1: 6 条真实锚点全部解析并真正校验、命中 =="
run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
E1_TOTAL=$(field "$OUT" total); E1_PARSED=$(field "$OUT" current_parsed)
E1_HIT=$(field "$OUT" current_verified_hit); E1_FAIL=$(field "$OUT" current_failed)
E1_OLD=$(field "$OUT" old_format); E1_UNP=$(field "$OUT" unparseable)
if [ "$RC" -eq 0 ] && [ "$E1_TOTAL" -eq 6 ] && [ "$E1_PARSED" -eq 6 ] \
   && [ "$E1_HIT" -eq 6 ] && [ "$E1_FAIL" -eq 0 ] \
   && [ "$E1_OLD" -eq 0 ] && [ "$E1_UNP" -eq 0 ]; then ok "E1"; else bad "E1 (rc=$RC $OUT)"; fi

echo "== E2: 131 条历史裸 path:line 全部显式归入旧格式 =="
run_tool --corpus "$HERE/fixtures/bare-path-line.131.export.json" --json
E2_TOTAL=$(field "$OUT" total); E2_OLD=$(field "$OUT" old_format)
E2_CUR=$(field "$OUT" current_parsed); E2_UNP=$(field "$OUT" unparseable)
if [ "$E2_TOTAL" -eq 131 ] && [ "$E2_OLD" -eq 131 ] && [ "$E2_CUR" -eq 0 ] && [ "$E2_UNP" -eq 0 ]; then ok "E2"; else bad "E2 ($OUT)"; fi

echo "== E3: 变异——正则改回 repo@sha:path 形态 ⇒ E1 必须挂 =="
BACKUP="$(mktemp)"; cp "$TOOL" "$BACKUP"
"$PY" - "$TOOL" <<'PY'
import sys
p=sys.argv[1]
s=open(p).read()
old="r'^code://([^@]+)@([0-9a-fA-F]{7,40})#L(\\d+)(?:-L?(\\d+))?$'"
new="r'^code://([^@]+)@([^:]+):(.+)#L(\\d+)(?:-L?(\\d+))?$'"
if old not in s:
    print("MUTATION-ANCHOR-MISS"); sys.exit(9)
open(p,'w').write(s.replace(old,new))
PY
if [ "$?" -ne 0 ]; then bad "E3 变异未命中正则（无证据）"; else
  run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
  E3_HIT=$(field "$OUT" current_verified_hit); E3_UNP=$(field "$OUT" unparseable)
  if [ "$E3_HIT" -eq 0 ] && [ "$E3_UNP" -eq 6 ]; then ok "E3"; else bad "E3 (hit=$E3_HIT unparseable=$E3_UNP)"; fi
  cp "$BACKUP" "$TOOL"
  # 还原后必须与变异前备份逐字一致（变异不得残留在工作区）
  if cmp -s "$BACKUP" "$TOOL"; then ok "E3-restore"; else bad "E3-restore 未还原"; fi
fi

echo "== E4: 引文不在该位置 ⇒ 判失败 =="
run_tool --corpus "$HERE/fixtures/control-mismatch.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
E4_HIT=$(field "$OUT" current_verified_hit); E4_FAIL=$(field "$OUT" current_failed)
if [ "$E4_FAIL" -ge 1 ] && [ "$E4_HIT" -eq 0 ]; then ok "E4"; else bad "E4 (fail=$E4_FAIL hit=$E4_HIT)"; fi

echo "== E5: 与 E4 只差一项（引文在）⇒ 判通过 =="
run_tool --corpus "$HERE/fixtures/control-match.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
E5_HIT=$(field "$OUT" current_verified_hit); E5_FAIL=$(field "$OUT" current_failed)
if [ "$E5_HIT" -ge 1 ] && [ "$E5_FAIL" -eq 0 ]; then ok "E5"; else bad "E5 (hit=$E5_HIT fail=$E5_FAIL)"; fi

echo "== E6: 缺 --repo-root ⇒ 响亮失败、非零退出、不猜仓 =="
run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --json
E6_RC="$RC"; E6_OUT="$OUT"
if [ "$E6_RC" -ne 0 ] && grep -q "repo-root" "$E6_OUT"; then ok "E6"; else bad "E6 (rc=$E6_RC)"; fi

echo "== E7: fetcher 取不到该 revision ⇒ 响亮失败、区别于「不匹配」 =="
run_tool --corpus "$HERE/fixtures/control-missing-rev.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
E7_RC="$RC"; E7_OUT="$OUT"
if [ "$E7_RC" -eq 2 ] && grep -qiE "fetcher|取不到|取回空" "$E7_OUT"; then ok "E7"; else bad "E7 (rc=$E7_RC)"; fi

echo "== E8: 三类计数分别输出且总和 === 输入条数 =="
run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
E8A_TOTAL=$(field "$OUT" total); E8A_PARSED=$(field "$OUT" current_parsed)
E8A_OLD=$(field "$OUT" old_format); E8A_UNP=$(field "$OUT" unparseable); E8A_OK=$(field "$OUT" sums_ok)
run_tool --corpus "$HERE/fixtures/bare-path-line.131.export.json" --json
E8B_TOTAL=$(field "$OUT" total); E8B_PARSED=$(field "$OUT" current_parsed)
E8B_OLD=$(field "$OUT" old_format); E8B_UNP=$(field "$OUT" unparseable); E8B_OK=$(field "$OUT" sums_ok)
if [ "$E8A_OK" = "True" ] && [ "$E8B_OK" = "True" ] \
   && [ "$((E8A_PARSED+E8A_OLD+E8A_UNP))" -eq "$E8A_TOTAL" ] \
   && [ "$((E8B_PARSED+E8B_OLD+E8B_UNP))" -eq "$E8B_TOTAL" ]; then ok "E8"; else bad "E8 ($OUT)"; fi

echo
echo "selftest: pass=$pass fail=$fail"
if [ "$fail" -eq 0 ]; then echo "E9 PASS"; exit 0; else echo "E9 FAIL"; exit 1; fi