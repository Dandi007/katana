#!/usr/bin/env bash
# anchor-check-selftest.sh — N2b 硬验收（E1–E9）+ E3 包 web:// 硬验收（W1–W9）自检入口
# （E9：本路径必须存在且 exit 0）
#
# 判据：校验器必须以**生产者的实际格式**为准：
#   code://<path>@<sha>#L<a>[-L<b>]  （locator 为仓内相对路径，repo 归属由 --repo-root 外部提供）
#   web://<uri>@<64位hex>#<range>    （uri 未编码且自身含 `://`；核验源由 --content-* 外部提供）
# 五类锚点显式分类、分别计数、总和 === 输入条数；
# 缺失 repo-root / 缺 transcript 源 / fetcher 取不到 / 空内容 / range 认不出 → 响亮失败。
#
# E1..E8 在此求值；E9 即本脚本自身（存在且 exit 0）；E10（不碰 .dd-evidence/）由提交边界保证。
# W1..W9 求值 web:// 包的判据 2–7 与「⛔ 不联网抓 <uri>」这条最重要的设计约束。
#
# fixtures/ 里 web:// 相关的三类数据说明（provenance，如实记录）：
#   - anchor 本身：`web://http://127.0.0.1:50287/e1-material2.png@9bee527f…#L3:1-43` 等
#     **逐字取自派发方 2026-08-14 真机跑出的证据 channel**（spec §0 GT-2），⛔ 未作任何改造；
#     W4 只把其中的**端口**换成本机临时监听端口（形态不变），因为要真起一个 sniffer。
#   - transcript（fixtures/web-transcripts.export.json）：**自检自备**的预置件
#     （spec 判据 3 原话「预置一份 transcript」），digest 与上述真 anchor 一致、
#     body 为 ASCII 以免字符区间口径受编码影响；⛔ 它不冒充真机产物。
#   - quote：从该 transcript 的对应 range **切出来**的逐字子串（不是照着写的）。
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
pyq(){ # $1=file $2=python 表达式（d = 解析后的 JSON）——用于取嵌套字段
  "$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(eval(sys.argv[2]))" "$1" "$2"
}

# ── 变异工具：把被测源码里某个逐字片段换掉，回显被改的行（变异未命中即无证据）──
MUT_BACKUP="$(mktemp)"
mutate(){ # $1=旧逐字片段 $2=新片段
  cp "$TOOL" "$MUT_BACKUP"
  "$PY" - "$TOOL" "$1" "$2" <<'PY'
import sys
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
if old not in s:
    print("MUTATION-ANCHOR-MISS"); sys.exit(9)
s2 = s.replace(old, new, 1)
open(path, "w").write(s2)
for line in s2.splitlines():
    if new.strip() in line:
        print("MUTATED-LINE: " + line.strip()); break
PY
}
unmutate(){ cp "$MUT_BACKUP" "$TOOL"; }

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


# ══ E3 包（web://）：W1..W9 ══
WEB3="$HERE/fixtures/web-anchors.3.export.json"
DOCS="$HERE/fixtures/web-transcripts.export.json"
GT_ANCHOR='web://http://127.0.0.1:50287/e1-material2.png@9bee527fe5f6e5ddef93194f3ede333b964ff9b50c8db013aef1dc6659fe1675#L3:1-43'

echo "== W1: 判据2 —— 真机逐字 anchor 判为 web，且 uri/digest/range 三段解析正确 =="
run_tool --corpus "$WEB3" --classify-only
W1_A0=$(pyq "$OUT" 'd["entries"][0]["anchor"]')
W1_KINDS=$(pyq "$OUT" '",".join(e["kind"] for e in d["entries"])')
W1_URI=$(pyq "$OUT" 'd["entries"][0]["parsed"]["uri"]')
W1_DIG=$(pyq "$OUT" 'd["entries"][0]["parsed"]["digest"]')
W1_RNG=$(pyq "$OUT" 'd["entries"][0]["parsed"]["range"]')
W1_RNG2=$(pyq "$OUT" 'd["entries"][1]["parsed"]["range"]')
W1_RNG3=$(pyq "$OUT" 'd["entries"][2]["parsed"]["range"]')
if [ "$W1_A0" = "$GT_ANCHOR" ] && [ "$W1_KINDS" = "web,web,web" ] \
   && [ "$W1_URI" = "http://127.0.0.1:50287/e1-material2.png" ] \
   && [ "$W1_DIG" = "9bee527fe5f6e5ddef93194f3ede333b964ff9b50c8db013aef1dc6659fe1675" ] \
   && [ "${#W1_DIG}" -eq 64 ] \
   && [ "$W1_RNG" = "L3:1-43" ] && [ "$W1_RNG2" = "L7:12-308" ] && [ "$W1_RNG3" = "L9" ]; then
  ok "W1"
else
  bad "W1 (kinds=$W1_KINDS uri=$W1_URI dig=$W1_DIG rng=$W1_RNG/$W1_RNG2/$W1_RNG3)"
fi

echo "== W1b: URI 段贪婪 —— uri 自身含 '@' 时仍以「结尾定长 64 hex digest」切分 =="
# ⚠️ 如实记录：真机 16 条 uri 里**没有** `@`，`[^@]+` 在那批样本上碰巧也能过
#    （非贪婪 `.+?` 在 digest 定长 + 尾锚定下更是与贪婪完全等价）。
#    要让「URI 段必须贪婪」这条契约具备判别力，必须有一条 uri 内含 `@` 的输入
#    ⇒ fixtures/web-at-in-uri.export.json 是**合成控制输入**，⛔ 不冒充真机产物。
ATURI="$HERE/fixtures/web-at-in-uri.export.json"
run_tool --corpus "$ATURI" --classify-only
W1B_KIND=$(pyq "$OUT" 'd["entries"][0]["kind"]')
W1B_URI=$(pyq "$OUT" 'd["entries"][0]["parsed"]["uri"] if d["entries"][0]["parsed"] else None')
run_tool --corpus "$ATURI" --content-export "$DOCS" --json
W1B_HIT=$(field "$OUT" web_verified_hit)
if [ "$W1B_KIND" = "web" ] && [ "$W1B_URI" = "http://127.0.0.1:50287/e1-material2@2x.png" ] \
   && [ "$RC" -eq 0 ] && [ "$W1B_HIT" -eq 1 ]; then
  ok "W1b"
else bad "W1b (kind=$W1B_KIND uri=$W1B_URI rc=$RC hit=$W1B_HIT)"; fi

echo "== W1m: 变异——URI 段改成 [^@]+ ⇒ W1b 必须挂 =="
mutate '^web://(.+)@' '^web://([^@]+)@' > "$MUT"
if grep -q "MUTATION-ANCHOR-MISS" "$MUT"; then
  bad "W1m 变异未命中 WEB_URI_RE（无证据）"
else
  echo "  W1m 变异已落到: $(grep 'MUTATED-LINE' "$MUT" | head -1)"
  run_tool --corpus "$ATURI" --classify-only
  W1M_KIND=$(pyq "$OUT" 'd["entries"][0]["kind"]')
  run_tool --corpus "$ATURI" --content-export "$DOCS" --json
  W1M_HIT=$(field "$OUT" web_verified_hit)
  if [ "$W1M_KIND" != "web" ] && [ "$W1M_HIT" -eq 0 ]; then
    ok "W1m (变异后判为 $W1M_KIND、命中归零)"
  else bad "W1m (kind=$W1M_KIND hit=$W1M_HIT)"; fi
  unmutate
fi

echo "== W1d: 变异——digest 宽度放宽成 {7,40}（沿用 code:// 的宽度）⇒ 解析必须挂 =="
mutate '([0-9a-fA-F]{64})#(.+)$' '([0-9a-fA-F]{7,40})#(.+)$' > "$MUT"
if grep -q "MUTATION-ANCHOR-MISS" "$MUT"; then
  bad "W1d 变异未命中 digest 宽度（无证据）"
else
  echo "  W1d 变异已落到: $(grep 'MUTATED-LINE' "$MUT" | head -1)"
  run_tool --corpus "$WEB3" --content-export "$DOCS" --json
  W1D_HIT=$(field "$OUT" web_verified_hit)
  if [ "$W1D_HIT" -ne 3 ]; then ok "W1d (变异后命中 $W1D_HIT/3)"; else bad "W1d (hit 仍为 3)"; fi
  unmutate
fi

echo "== W2: 判据3 正向 —— 预置 transcript（digest 一致）⇒ 命中、计入 web_verified_hit =="
run_tool --corpus "$WEB3" --content-export "$DOCS" --json
W2_P=$(field "$OUT" web_parsed); W2_H=$(field "$OUT" web_verified_hit)
W2_F=$(field "$OUT" web_failed); W2_S=$(field "$OUT" sums_ok)
if [ "$RC" -eq 0 ] && [ "$W2_P" -eq 3 ] && [ "$W2_H" -eq 3 ] && [ "$W2_F" -eq 0 ] && [ "$W2_S" = "True" ]; then
  ok "W2"
else bad "W2 (rc=$RC parsed=$W2_P hit=$W2_H fail=$W2_F sums=$W2_S)"; fi

echo "== W3: 判据3 反向 —— digest 在 research:content 上不存在 ⇒ 响亮失败(exit 2)，⛔ 不是「引文不匹配」 =="
run_tool --corpus "$HERE/fixtures/web-absent-digest.export.json" --content-export "$DOCS" --json
W3_F=$(field "$OUT" web_failed); W3_H=$(field "$OUT" web_verified_hit)
W3_L=$(pyq "$OUT" 'len(d["loud_failures"])')
if [ "$RC" -eq 2 ] && [ "$W3_L" -ge 1 ] && [ "$W3_F" -eq 0 ] && [ "$W3_H" -eq 0 ]; then
  ok "W3"
else bad "W3 (rc=$RC loud=$W3_L web_failed=$W3_F)"; fi

echo "== W3b: D3 纪律 —— 不给 transcript 源 ⇒ 响亮失败（与 --repo-root 同纪律，⛔ 绝不猜、绝不联网） =="
run_tool --corpus "$WEB3" --json
W3B_L=$(pyq "$OUT" 'len(d["loud_failures"])')
if [ "$RC" -eq 2 ] && [ "$W3B_L" -eq 3 ] && grep -q -- "--content-channel" "$OUT"; then
  ok "W3b"
else bad "W3b (rc=$RC loud=$W3B_L)"; fi

echo "== W4: ⭐ 判据3 反向 —— 核验全程不对 <uri> 发起任何网络请求 =="
NETDIR="$(mktemp -d)"
printf 'fake material bytes for the sniffer\n' > "$NETDIR/e1-material2.png"
NETLOG="$NETDIR/access.log"
PORT="$("$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));p=s.getsockname()[1];s.close();print(p)')"
"$PY" -m http.server "$PORT" --bind 127.0.0.1 --directory "$NETDIR" >/dev/null 2>"$NETLOG" &
SRV_PID=$!
hits(){ grep -c '"GET' "$NETLOG" 2>/dev/null || true; }
# 传感器自检：真发一次请求，日志必须涨——否则后面的「没涨」什么都不证明
CTRL=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if "$PY" -c "import urllib.request,sys;urllib.request.urlopen('http://127.0.0.1:$PORT/e1-material2.png',timeout=2).read()" >/dev/null 2>&1; then CTRL=1; break; fi
  sleep 0.3
done
NETCORPUS="$NETDIR/corpus.json"
"$PY" - "$WEB3" "$NETCORPUS" "$PORT" <<'PY'
import json, sys
src, dst, port = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(src))
for e in data:  # 只换端口：anchor 形态与真机逐字一致
    e["anchor"] = e["anchor"].replace("127.0.0.1:50287", "127.0.0.1:" + port)
json.dump(data, open(dst, "w"), ensure_ascii=False, indent=2)
PY
BEFORE_HITS="$(hits)"
run_tool --corpus "$NETCORPUS" --content-export "$DOCS" --json
W4_H=$(field "$OUT" web_verified_hit)
AFTER_HITS="$(hits)"
if [ "$CTRL" -eq 1 ] && [ "$BEFORE_HITS" -ge 1 ] && [ "$AFTER_HITS" -eq "$BEFORE_HITS" ] \
   && [ "$RC" -eq 0 ] && [ "$W4_H" -eq 3 ]; then
  ok "W4 (sniffer 命中控制请求 $BEFORE_HITS 次；核验期间 0 次新请求，hit=$W4_H)"
else
  bad "W4 (ctrl=$CTRL before=$BEFORE_HITS after=$AFTER_HITS rc=$RC hit=$W4_H)"
fi

echo "== W4m: 变异——改成联网抓 <uri> ⇒ W4 的断言必须变红（证明该断言真能发现联网） =="
mutate 'body, err = store.body_for(parsed["digest"])' \
       'body, err = (__import__("urllib.request", fromlist=["x"]).urlopen(parsed["uri"], timeout=5).read().decode("utf-8", "replace"), None)' > "$MUT"
if grep -q "MUTATION-ANCHOR-MISS" "$MUT"; then
  bad "W4m 变异未命中取材调用（无证据）"
else
  echo "  W4m 变异已落到: $(grep 'MUTATED-LINE' "$MUT" | head -1)"
  run_tool --corpus "$NETCORPUS" --content-export "$DOCS" --json
  MUT_HITS="$(hits)"
  if [ "$MUT_HITS" -gt "$AFTER_HITS" ]; then
    ok "W4m (联网实现下 sniffer 从 $AFTER_HITS 涨到 $MUT_HITS)"
  else
    bad "W4m (联网实现下 sniffer 仍为 $MUT_HITS ⇒ W4 的断言没有判别力)"
  fi
  unmutate
fi
kill "$SRV_PID" 2>/dev/null; wait "$SRV_PID" 2>/dev/null

echo "== W5: 判据4 —— transcript 为空 / range 定位不到 ⇒ 响亮失败，⛔ 不算未命中 =="
run_tool --corpus "$HERE/fixtures/web-empty-transcript.export.json" --content-export "$DOCS" --json
W5A_RC="$RC"; W5A_F=$(field "$OUT" web_failed); W5A_OUT="$OUT"
run_tool --corpus "$HERE/fixtures/web-unlocatable-range.export.json" --content-export "$DOCS" --json
W5B_RC="$RC"; W5B_F=$(field "$OUT" web_failed); W5B_L=$(pyq "$OUT" 'len(d["loud_failures"])')
if [ "$W5A_RC" -eq 2 ] && [ "$W5A_F" -eq 0 ] && grep -qiE "空 transcript|形态不合理" "$W5A_OUT" \
   && [ "$W5B_RC" -eq 2 ] && [ "$W5B_F" -eq 0 ] && [ "$W5B_L" -eq 2 ]; then
  ok "W5"
else bad "W5 (empty rc=$W5A_RC fail=$W5A_F / unlocatable rc=$W5B_RC fail=$W5B_F loud=$W5B_L)"; fi

echo "== W6: 判据7 —— range 语法认不出 ⇒ 响亮失败，⛔ 不得静默命中/未命中 =="
run_tool --corpus "$HERE/fixtures/web-bad-range.export.json" --content-export "$DOCS" --json
W6_H=$(field "$OUT" web_verified_hit); W6_F=$(field "$OUT" web_failed)
if [ "$RC" -eq 2 ] && [ "$W6_H" -eq 0 ] && [ "$W6_F" -eq 0 ] && grep -q "认不出的 range 语法" "$OUT"; then
  ok "W6"
else bad "W6 (rc=$RC hit=$W6_H fail=$W6_F)"; fi

echo "== W6b: D8 —— 引文真的不在该 range ⇒ 未命中(exit 1)，与响亮失败(exit 2) 分得开 =="
run_tool --corpus "$HERE/fixtures/web-mismatch.export.json" --content-export "$DOCS" --json
W6B_H=$(field "$OUT" web_verified_hit); W6B_F=$(field "$OUT" web_failed)
W6B_L=$(pyq "$OUT" 'len(d["loud_failures"])')
if [ "$RC" -eq 1 ] && [ "$W6B_H" -eq 0 ] && [ "$W6B_F" -eq 1 ] && [ "$W6B_L" -eq 0 ]; then
  ok "W6b"
else bad "W6b (rc=$RC hit=$W6B_H fail=$W6B_F loud=$W6B_L)"; fi

echo "== W7: 判据5 —— wiki:// / feishu:// 计入 unsupported_scheme（不进 unparseable、不致非零退出） =="
UNS="$HERE/fixtures/unsupported-scheme.export.json"
run_tool --corpus "$UNS" --json
W7_U=$(field "$OUT" unsupported_scheme); W7_N=$(field "$OUT" unparseable); W7_S=$(field "$OUT" sums_ok)
if [ "$RC" -eq 0 ] && [ "$W7_U" -eq 2 ] && [ "$W7_N" -eq 1 ] && [ "$W7_S" = "True" ]; then
  ok "W7"
else bad "W7 (rc=$RC unsupported=$W7_U unparseable=$W7_N sums=$W7_S)"; fi

echo "== W7h: D7 —— 人读输出也必须显式披露 unsupported_scheme 条数 =="
run_tool --corpus "$UNS"
if grep -q "unsupported_scheme" "$OUT" && grep -qE "unsupported_scheme[^0-9]*: 2" "$OUT"; then
  ok "W7h"
else bad "W7h (人读输出未显式披露 unsupported_scheme 条数: $OUT)"; fi

echo "== W7m: 变异——把 unsupported_scheme 并回 unparseable ⇒ W7 必须挂 =="
mutate 'return "unsupported_scheme", {"scheme": m.group(1)}' 'return "unparseable", None' > "$MUT"
if grep -q "MUTATION-ANCHOR-MISS" "$MUT"; then
  bad "W7m 变异未命中分类分支（无证据）"
else
  echo "  W7m 变异已落到: $(grep 'MUTATED-LINE' "$MUT" | head -1)"
  run_tool --corpus "$UNS" --json
  W7M_U=$(field "$OUT" unsupported_scheme); W7M_N=$(field "$OUT" unparseable)
  if [ "$W7M_U" -ne 2 ] && [ "$W7M_N" -eq 3 ]; then ok "W7m"; else bad "W7m (unsupported=$W7M_U unparseable=$W7M_N)"; fi
  unmutate
fi

echo "== W8: 判据6 —— 五类齐全 + 一条缺 anchor ⇒ sums_ok=False 且 exit 3 =="
ALLC="$HERE/fixtures/all-classes.export.json"
run_tool --corpus "$ALLC" --content-export "$DOCS" --repo-root "$REPO_ROOT_OVERRIDE" --json
W8_C=$(field "$OUT" current_parsed); W8_W=$(field "$OUT" web_parsed); W8_O=$(field "$OUT" old_format)
W8_U=$(field "$OUT" unsupported_scheme); W8_N=$(field "$OUT" unparseable)
W8_D=$(field "$OUT" discarded); W8_S=$(field "$OUT" sums_ok); W8_T=$(field "$OUT" total)
if [ "$RC" -eq 3 ] && [ "$W8_S" = "False" ] && [ "$W8_T" -eq 6 ] && [ "$W8_D" -eq 1 ] \
   && [ "$W8_C" -eq 1 ] && [ "$W8_W" -eq 1 ] && [ "$W8_O" -eq 1 ] && [ "$W8_U" -eq 1 ] && [ "$W8_N" -eq 1 ]; then
  ok "W8"
else bad "W8 (rc=$RC sums=$W8_S total=$W8_T cur=$W8_C web=$W8_W old=$W8_O uns=$W8_U unp=$W8_N disc=$W8_D)"; fi

echo "== W8m: 变异——守恒式漏掉 unsupported_scheme ⇒ 本该成立的守恒必须变红 =="
mutate 'sums_ok = (cur_parsed + web_parsed + old_count + unsupported_scheme' \
       'sums_ok = (cur_parsed + web_parsed + old_count + 0 * unsupported_scheme' > "$MUT"
if grep -q "MUTATION-ANCHOR-MISS" "$MUT"; then
  bad "W8m 变异未命中守恒式（无证据）"
else
  echo "  W8m 变异已落到: $(grep 'MUTATED-LINE' "$MUT" | head -1)"
  run_tool --corpus "$UNS" --json
  W8M_S=$(field "$OUT" sums_ok)
  if [ "$W8M_S" = "False" ] && [ "$RC" -eq 3 ]; then ok "W8m"; else bad "W8m (sums=$W8M_S rc=$RC)"; fi
  unmutate
fi

echo "== W9: 回归 —— code:// 与旧格式在新增 --content-* 之后逐字不变 =="
run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --repo-root "$REPO_ROOT_OVERRIDE" \
         --content-export "$DOCS" --json
W9_P=$(field "$OUT" current_parsed); W9_H=$(field "$OUT" current_verified_hit)
W9_W=$(field "$OUT" web_parsed); W9_U=$(field "$OUT" unsupported_scheme)
W9A_RC="$RC"
run_tool --corpus "$HERE/fixtures/bare-path-line.131.export.json" --content-export "$DOCS" --json
W9_O=$(field "$OUT" old_format); W9_ON=$(field "$OUT" unparseable); W9_OU=$(field "$OUT" unsupported_scheme)
W9B_RC="$RC"
if [ "$W9A_RC" -eq 0 ] && [ "$W9_P" -eq 6 ] && [ "$W9_H" -eq 6 ] && [ "$W9_W" -eq 0 ] && [ "$W9_U" -eq 0 ] \
   && [ "$W9B_RC" -eq 0 ] && [ "$W9_O" -eq 131 ] && [ "$W9_ON" -eq 0 ] && [ "$W9_OU" -eq 0 ]; then
  ok "W9"
else bad "W9 (cur=$W9_P/$W9_H web=$W9_W uns=$W9_U | old=$W9_O unp=$W9_ON uns=$W9_OU)"; fi

echo "== W-restore: 全部变异已逐字还原（git diff --stat 必须为空） =="
WDIFF="$(git -C "$HERE" --no-pager diff --stat 2>/dev/null)"
if [ -z "$WDIFF" ]; then ok "W-restore"; else bad "W-restore 未还原: $WDIFF"; fi

echo
echo "selftest: pass=$pass fail=$fail"
if [ "$fail" -eq 0 ]; then echo "E9 PASS"; exit 0; else echo "E9 FAIL"; exit 1; fi