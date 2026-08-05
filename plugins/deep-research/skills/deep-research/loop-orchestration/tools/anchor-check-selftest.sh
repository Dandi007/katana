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

# E1..E8 需要一份含 fixture 所指 revision 与文件（src/tick.ts / src/tick-run.ts）
# 的真实仓根。不硬编码单个绝对路径：优先 ANCHOR_REPO_ROOT，否则在候选基下
# 发现并**验证**（确实含所需 revision+文件）才采用；找不到则响亮失败——保证
# E9 的 exit 0 是交付包性质而非环境偶然。
REQ_REV="a592276892f5e93a5e37d800a52dd48436639c0b"
repo_valid(){ # $1=候选仓根
  [ -d "$1" ] || return 1
  git -C "$1" cat-file -e "$REQ_REV^{commit}" 2>/dev/null || return 1
  git -C "$1" cat-file -e "$REQ_REV:src/tick.ts" 2>/dev/null || return 1
  git -C "$1" cat-file -e "$REQ_REV:src/tick-run.ts" 2>/dev/null || return 1
  return 0
}
pick_repo_root(){
  local cand
  if [ -n "${ANCHOR_REPO_ROOT:-}" ] && repo_valid "$ANCHOR_REPO_ROOT"; then
    echo "$ANCHOR_REPO_ROOT"; return 0
  fi
  for cand in /data/code/self/loop-engine-deep-research-plugin /data/code/self/*; do
    [ "$cand" = "/data/code/self/*" ] && continue
    if repo_valid "$cand"; then echo "$cand"; return 0; fi
  done
  return 1
}
REPO_ROOT_OVERRIDE="$(pick_repo_root)" || {
  echo "E9-FAIL: 找不到含所需 revision($REQ_REV) 与 src/tick.ts 的仓根；请设 ANCHOR_REPO_ROOT"
  exit 9
}

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
MUT="$(mktemp)"
"$PY" - "$TOOL" >"$MUT" 2>&1 <<'PY'
import sys
p=sys.argv[1]
s=open(p).read()
old="r'^code://([^@]+)@([0-9a-fA-F]{7,40})#L(\\d+)(?:-L?(\\d+))?$'"
new="r'^code://([^@]+)@([^:]+):(.+)#L(\\d+)(?:-L?(\\d+))?$'"
if old not in s:
    print("MUTATION-ANCHOR-MISS"); sys.exit(9)
s2=s.replace(old,new)
open(p,'w').write(s2)
# 回显被改的那一行（spec §3：破坏后必须回显被改行，变异未命中即无证据）
for line in s2.splitlines():
    if "^code://" in line and "@([^:]+):(.+)" in line:
        print("MUTATED-LINE: " + line.strip()); break
PY
if grep -q "MUTATION-ANCHOR-MISS" "$MUT"; then
  bad "E3 变异未命中正则（无证据）: $(cat "$MUT")"
else
  echo "  E3 变异已落到: $(grep 'MUTATED-LINE' "$MUT" | head -1)"
  run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
  E3_HIT=$(field "$OUT" current_verified_hit); E3_UNP=$(field "$OUT" unparseable)
  if [ "$E3_HIT" -eq 0 ] && [ "$E3_UNP" -eq 6 ]; then ok "E3"; else bad "E3 (hit=$E3_HIT unparseable=$E3_UNP)"; fi
  cp "$BACKUP" "$TOOL"
  # 还原后必须用 git diff --stat 确认工作区干净（spec §3：逐字还原并 git diff --stat 确认）
  DIFFSTAT="$(git -C "$HERE" --no-pager diff --stat 2>/dev/null)"
  if [ -z "$DIFFSTAT" ]; then
    ok "E3-restore (git diff --stat 为空，工作区干净)"
  else
    bad "E3-restore 未还原: $DIFFSTAT"
  fi
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