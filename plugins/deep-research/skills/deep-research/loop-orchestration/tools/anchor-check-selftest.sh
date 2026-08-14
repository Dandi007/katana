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

# 变异测试会就地改被测工具；无论怎么退出都必须还原成进场时的字节。
TOOL_PRISTINE="$(mktemp)"; cp "$TOOL" "$TOOL_PRISTINE"
trap 'cp "$TOOL_PRISTINE" "$TOOL"' EXIT

run_tool(){ # $1.. -> writes json to $OUT, exit code to $RC
  OUT="$(mktemp)"
  "$PY" "$TOOL" "$@" >"$OUT" 2>&1
  RC=$?
}
field(){ # $1=file $2=key
  "$PY" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get(sys.argv[2]))" "$1" "$2"
}

# ── 禁网沙箱（W3/W4 用）：拦下并**记账**一切 socket 连接，然后 runpy 原样驱动
#    anchor-check.py 本体。⛔ 不是重实现被测对象，跑的就是 SUT 自己。
GUARD="$(mktemp -t netguard.XXXXXX.py)"
cat >"$GUARD" <<'PYGUARD'
import os, runpy, socket, sys
LOG = os.environ["NETLOG"]; TOOL = os.environ["TOOL"]
def _record(addr):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("CONNECT %r\n" % (addr,))
def _blocked_connect(self, addr, *a, **k):
    _record(addr); raise OSError("NETWORK-BLOCKED-BY-SELFTEST")
def _blocked_create_connection(addr, *a, **k):
    _record(addr); raise OSError("NETWORK-BLOCKED-BY-SELFTEST")
socket.socket.connect = _blocked_connect
socket.socket.connect_ex = _blocked_connect
socket.create_connection = _blocked_create_connection
sys.argv = [TOOL] + sys.argv[1:]
runpy.run_path(TOOL, run_name="__main__")
PYGUARD

run_tool_netguard(){ # 同 run_tool，但在禁网沙箱里跑；连接尝试记到 $NETLOG
  OUT="$(mktemp)"; NETLOG="$(mktemp)"; : >"$NETLOG"
  NETLOG="$NETLOG" TOOL="$TOOL" "$PY" "$GUARD" "$@" >"$OUT" 2>&1
  RC=$?
}

# ── 变异工具：改不到锚点就是**无证据**，必须判 FAIL（不得静默放过）
apply_mutation(){ # $1=旧片段 $2=新片段
  MUT="$(mktemp)"
  MUT_OLD="$1" MUT_NEW="$2" "$PY" - "$TOOL" >"$MUT" 2>&1 <<'PYMUT'
import os, sys
p = sys.argv[1]; s = open(p).read()
old = os.environ["MUT_OLD"]; new = os.environ["MUT_NEW"]
if old not in s:
    print("MUTATION-ANCHOR-MISS"); sys.exit(9)
open(p, "w").write(s.replace(old, new, 1))
print("MUTATED-TO: " + new.strip().splitlines()[0])
PYMUT
  grep -q "MUTATION-ANCHOR-MISS" "$MUT" && return 1
  echo "    变异已落到: $(grep 'MUTATED-TO' "$MUT" | head -1)"
  return 0
}
restore_tool(){ # 还原并用 git diff --stat 确认工作区干净
  cp "$TOOL_PRISTINE" "$TOOL"
  local d; d="$(git -C "$HERE" --no-pager diff --stat -- "$TOOL" 2>/dev/null)"
  [ -z "$d" ] || { bad "$1-restore 未还原: $d"; return 1; }
  return 0
}
WEB_STORE="$HERE/fixtures/web-content.transcripts.json"

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
echo "######## E3 包：web:// 离线核验 + unsupported_scheme 显式单列 ########"

echo "== W1: D1/D2 正向——GT-2 逐字锚点 + 三种 range 语法全部解析并真正命中 =="
run_tool --corpus "$HERE/fixtures/web-anchors.5.export.json" --content-source "$WEB_STORE" --json
W1_TOT=$(field "$OUT" total); W1_WP=$(field "$OUT" web_parsed)
W1_WH=$(field "$OUT" web_verified_hit); W1_WF=$(field "$OUT" web_failed)
W1_UNS=$(field "$OUT" unsupported_scheme); W1_UNP=$(field "$OUT" unparseable)
W1_OLD=$(field "$OUT" old_format); W1_CUR=$(field "$OUT" current_parsed)
if [ "$RC" -eq 0 ] && [ "$W1_TOT" -eq 5 ] && [ "$W1_WP" -eq 5 ] && [ "$W1_WH" -eq 5 ] \
   && [ "$W1_WF" -eq 0 ] && [ "$W1_UNS" -eq 0 ] && [ "$W1_UNP" -eq 0 ] \
   && [ "$W1_OLD" -eq 0 ] && [ "$W1_CUR" -eq 0 ]; then ok "W1"; else bad "W1 (rc=$RC $OUT)"; fi

echo "== W2: D1 变异——URI 段收窄 / digest 宽度放宽 ⇒ 都必须变红 =="
# 取证结论（本包实测，⛔ 未照抄 spec §0 的建议行）：真实锚点里只有一个 `@` 时，
# 非贪婪 `(.+?)` 会回溯到正确的 `@`，与贪婪等价、**不会**变红；真正会变红的是
# 收窄字符类：`[^@]+`（URI 的 userinfo 含 @）与 `[^:]+`（URI 自身含 `://`）。
W2_ALL_RED=1
mut_case(){ # $1=标签 $2=旧 $3=新
  if apply_mutation "$2" "$3"; then
    run_tool --corpus "$HERE/fixtures/web-anchors.5.export.json" --content-source "$WEB_STORE" --json
    local wh; wh=$(field "$OUT" web_verified_hit)
    if [ "$wh" = "5" ]; then echo "    $1: 仍 5 命中 ⇒ 未变红"; W2_ALL_RED=0
    else echo "    $1: web_verified_hit=$wh ⇒ 已变红"; fi
    restore_tool "W2-$1" || W2_ALL_RED=0
  else
    echo "    $1: 变异未命中锚点（无证据）"; W2_ALL_RED=0
  fi
}
mut_case "URI段->[^@]+" \
  "r'^web://(.+)@([0-9a-fA-F]{64})#(.+)\$'" "r'^web://([^@]+)@([0-9a-fA-F]{64})#(.+)\$'"
mut_case "URI段->[^:]+" \
  "r'^web://(.+)@([0-9a-fA-F]{64})#(.+)\$'" "r'^web://([^:]+)@([0-9a-fA-F]{64})#(.+)\$'"
mut_case "digest宽度->{7,40}" \
  "r'^web://(.+)@([0-9a-fA-F]{64})#(.+)\$'" "r'^web://(.+)@([0-9a-fA-F]{7,40})#(.+)\$'"
[ "$W2_ALL_RED" -eq 1 ] && ok "W2" || bad "W2（存在未变红的变异）"

echo "== W3: ⭐D3 正向——按 bus 上的 transcript 离线核验，全程未联网抓取 <uri> =="
run_tool_netguard --corpus "$HERE/fixtures/web-anchors.5.export.json" \
  --content-source "$WEB_STORE" --json
W3_RC="$RC"; W3_HIT=$(field "$OUT" web_verified_hit); W3_NET="$NETLOG"
if [ "$W3_RC" -eq 0 ] && [ "$W3_HIT" -eq 5 ] && [ ! -s "$W3_NET" ]; then ok "W3"
else bad "W3 (rc=$W3_RC hit=$W3_HIT 连接尝试=$(wc -l <"$W3_NET"))"; fi

echo "== W4: ⭐D3 反向变异——改成联网抓取 <uri> ⇒ W3 的「无网络」断言必须变红 =="
if apply_mutation \
  '            doc, err = content_store.get_transcript(parsed["digest"])' \
  '            import urllib.request as _u; _u.urlopen(parsed["uri"], timeout=5)
            doc, err = content_store.get_transcript(parsed["digest"])'; then
  run_tool_netguard --corpus "$HERE/fixtures/web-anchors.5.export.json" \
    --content-source "$WEB_STORE" --json
  if [ -s "$NETLOG" ]; then
    echo "    联网抓取被记账: $(head -1 "$NETLOG")"
    restore_tool "W4" && ok "W4"
  else
    bad "W4（联网变异后 NETLOG 仍为空 ⇒ 无网络断言不具判别性）"; restore_tool "W4"
  fi
else bad "W4 变异未命中锚点（无证据）"; fi

echo "== W5: ⭐D3 反向——digest 不在 research:content 上 ⇒ 响亮失败，⛔ 不记成引文不匹配 =="
run_tool --corpus "$HERE/fixtures/web-missing-digest.export.json" --content-source "$WEB_STORE" --json
W5_RC="$RC"; W5_WF=$(field "$OUT" web_failed); W5_WP=$(field "$OUT" web_parsed)
if [ "$W5_RC" -eq 2 ] && [ "$W5_WF" -eq 0 ] && [ "$W5_WP" -eq 1 ] \
   && grep -q "取不到 transcript" "$OUT"; then ok "W5"; else bad "W5 (rc=$W5_RC web_failed=$W5_WF)"; fi
if apply_mutation \
  '            doc, err = content_store.get_transcript(parsed["digest"])
            if err:
                loud_failures.append((anchor, err))
                continue' \
  '            doc, err = content_store.get_transcript(parsed["digest"])
            if err:
                web_fail += 1
                continue'; then
  run_tool --corpus "$HERE/fixtures/web-missing-digest.export.json" --content-source "$WEB_STORE" --json
  if [ "$RC" -ne 2 ] && [ "$(field "$OUT" web_failed)" -eq 1 ]; then
    restore_tool "W5-mut" && ok "W5-mut（记成不匹配 ⇒ 变红）"
  else bad "W5-mut 未变红 (rc=$RC)"; restore_tool "W5-mut"; fi
else bad "W5-mut 变异未命中锚点（无证据）"; fi

echo "== W6: D4——transcript 取回为空 / range 定位不到 ⇒ 响亮失败，⛔ 不算未命中 =="
run_tool --corpus "$HERE/fixtures/web-empty-transcript.export.json" --content-source "$WEB_STORE" --json
W6A_RC="$RC"; W6A_WF=$(field "$OUT" web_failed); W6A_OUT="$OUT"
run_tool --corpus "$HERE/fixtures/web-range-unlocatable.export.json" --content-source "$WEB_STORE" --json
W6B_RC="$RC"; W6B_WF=$(field "$OUT" web_failed); W6B_WP=$(field "$OUT" web_parsed)
if [ "$W6A_RC" -eq 2 ] && [ "$W6A_WF" -eq 0 ] && grep -q "取回空 transcript" "$W6A_OUT" \
   && [ "$W6B_RC" -eq 2 ] && [ "$W6B_WF" -eq 0 ] && [ "$W6B_WP" -eq 2 ] \
   && grep -q "无法定位" "$OUT"; then ok "W6"
else bad "W6 (空:rc=$W6A_RC/fail=$W6A_WF  定位:rc=$W6B_RC/fail=$W6B_WF)"; fi

echo "== W7: ⭐D2 反向——range 语法认不出 ⇒ 响亮失败，⛔ 不得静默命中/未命中 =="
run_tool --corpus "$HERE/fixtures/web-bad-range.export.json" --content-source "$WEB_STORE" --json
W7_RC="$RC"; W7_WP=$(field "$OUT" web_parsed); W7_WH=$(field "$OUT" web_verified_hit)
W7_WF=$(field "$OUT" web_failed); W7_SUM=$(field "$OUT" sums_ok)
if [ "$W7_RC" -eq 2 ] && [ "$W7_WP" -eq 1 ] && [ "$W7_WH" -eq 0 ] && [ "$W7_WF" -eq 0 ] \
   && [ "$W7_SUM" = "True" ] && grep -q "range 语法认不出" "$OUT"; then ok "W7"
else bad "W7 (rc=$W7_RC parsed=$W7_WP hit=$W7_WH fail=$W7_WF sums=$W7_SUM)"; fi
if apply_mutation \
  '                loud_failures.append(
                    (anchor, f"range 语法认不出: #{parsed['"'"'range'"'"']}"
                             f"（⛔ 不得静默命中、⛔ 不得静默未命中）"))
                continue' \
  '                web_fail += 1
                continue'; then
  run_tool --corpus "$HERE/fixtures/web-bad-range.export.json" --content-source "$WEB_STORE" --json
  if [ "$RC" -ne 2 ]; then restore_tool "W7-mut" && ok "W7-mut（静默未命中 ⇒ 变红）"
  else bad "W7-mut 未变红 (rc=$RC)"; restore_tool "W7-mut"; fi
else bad "W7-mut 变异未命中锚点（无证据）"; fi

echo "== W8: ⭐D5——unsupported_scheme 与 unparseable 必须分开，且不导致非零退出 =="
run_tool --corpus "$HERE/fixtures/unsupported-vs-unparseable.export.json" --json
W8_RC="$RC"; W8_UNS=$(field "$OUT" unsupported_scheme); W8_UNP=$(field "$OUT" unparseable)
W8_SUM=$(field "$OUT" sums_ok)
if [ "$W8_RC" -eq 0 ] && [ "$W8_UNS" -eq 2 ] && [ "$W8_UNP" -eq 1 ] && [ "$W8_SUM" = "True" ]; then
  ok "W8"; else bad "W8 (rc=$W8_RC unsupported=$W8_UNS unparseable=$W8_UNP)"; fi
if apply_mutation \
  '        return "unsupported_scheme", {"scheme": m.group(1)}' \
  '        return "unparseable", None'; then
  run_tool --corpus "$HERE/fixtures/unsupported-vs-unparseable.export.json" --json
  if [ "$(field "$OUT" unsupported_scheme)" -eq 0 ] && [ "$(field "$OUT" unparseable)" -eq 3 ]; then
    restore_tool "W8-mut" && ok "W8-mut（两类合并 ⇒ 变红）"
  else bad "W8-mut 未变红"; restore_tool "W8-mut"; fi
else bad "W8-mut 变异未命中锚点（无证据）"; fi

echo "== W9: ⭐D6——五类齐全 + 一条缺 anchor ⇒ sums_ok=False 且 exit 3 =="
run_tool --corpus "$HERE/fixtures/five-classes-plus-discarded.export.json" \
  --content-source "$WEB_STORE" --repo-root "$REPO_ROOT_OVERRIDE" --json
W9_RC="$RC"; W9_SUM=$(field "$OUT" sums_ok); W9_TOT=$(field "$OUT" total)
W9_CUR=$(field "$OUT" current_parsed); W9_WEB=$(field "$OUT" web_parsed)
W9_OLD=$(field "$OUT" old_format); W9_UNS=$(field "$OUT" unsupported_scheme)
W9_UNP=$(field "$OUT" unparseable); W9_DIS=$(field "$OUT" discarded)
if [ "$W9_RC" -eq 3 ] && [ "$W9_SUM" = "False" ] && [ "$W9_TOT" -eq 6 ] \
   && [ "$W9_CUR" -eq 1 ] && [ "$W9_WEB" -eq 1 ] && [ "$W9_OLD" -eq 1 ] \
   && [ "$W9_UNS" -eq 1 ] && [ "$W9_UNP" -eq 1 ] && [ "$W9_DIS" -eq 1 ]; then ok "W9"
else bad "W9 (rc=$W9_RC sums=$W9_SUM 五类=$W9_CUR/$W9_WEB/$W9_OLD/$W9_UNS/$W9_UNP disc=$W9_DIS)"; fi
if apply_mutation \
  '    sums_ok = ((cur_parsed + web_parsed + old_count + unsupported + unparseable + discarded)' \
  '    sums_ok = ((cur_parsed + web_parsed + old_count + unparseable + discarded)'; then
  run_tool --corpus "$HERE/fixtures/unsupported-vs-unparseable.export.json" --json
  if [ "$RC" -eq 3 ] && [ "$(field "$OUT" sums_ok)" = "False" ]; then
    restore_tool "W9-mut" && ok "W9-mut（守恒式漏掉 unsupported_scheme ⇒ 变红）"
  else bad "W9-mut 未变红 (rc=$RC)"; restore_tool "W9-mut"; fi
else bad "W9-mut 变异未命中锚点（无证据）"; fi

echo "== W10: D3 纪律——缺 --content-source ⇒ 响亮失败，⛔ 不猜、⛔ 不改成联网 =="
run_tool_netguard --corpus "$HERE/fixtures/web-anchors.5.export.json" --json
if [ "$RC" -eq 2 ] && grep -q "content-source" "$OUT" && [ ! -s "$NETLOG" ]; then ok "W10"
else bad "W10 (rc=$RC 连接尝试=$(wc -l <"$NETLOG"))"; fi

echo "== W11: D7——JSON 四个新字段齐全，且人读输出报告头显式披露 =="
run_tool --corpus "$HERE/fixtures/web-anchors.5.export.json" --content-source "$WEB_STORE" --json
W11_KEYS=$("$PY" -c "
import json,sys
d=json.load(open(sys.argv[1]))
need=['web_parsed','web_verified_hit','web_failed','unsupported_scheme']
print('OK' if all(k in d for k in need) else 'MISSING:'+str([k for k in need if k not in d]))" "$OUT")
run_tool --corpus "$HERE/fixtures/unsupported-vs-unparseable.export.json"
W11_HUMAN="$OUT"
if [ "$W11_KEYS" = "OK" ] && grep -q "web 格式已解析" "$W11_HUMAN" \
   && grep -q "本期不支持的 scheme: 2" "$W11_HUMAN"; then ok "W11"
else bad "W11 (json=$W11_KEYS human=$W11_HUMAN)"; fi

echo "== W12: 回归——code:// 与旧格式在新版下逐字不变 =="
run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
W12_A="$(field "$OUT" current_verified_hit)/$(field "$OUT" unsupported_scheme)/$(field "$OUT" web_parsed)/$RC"
run_tool --corpus "$HERE/fixtures/bare-path-line.131.export.json" --json
W12_B="$(field "$OUT" old_format)/$(field "$OUT" unsupported_scheme)/$(field "$OUT" web_parsed)/$RC"
run_tool --corpus "$HERE/fixtures/control-missing-rev.export.json" --repo-root "$REPO_ROOT_OVERRIDE" --json
W12_C="$RC"
run_tool --corpus "$HERE/fixtures/tick-reclaim.6.export.json" --json
W12_D="$RC"
if [ "$W12_A" = "6/0/0/0" ] && [ "$W12_B" = "131/0/0/0" ] && [ "$W12_C" -eq 2 ] \
   && [ "$W12_D" -eq 2 ]; then ok "W12"; else bad "W12 ($W12_A | $W12_B | $W12_C | $W12_D)"; fi

echo
echo "selftest: pass=$pass fail=$fail"
if [ "$fail" -eq 0 ]; then echo "E9 PASS"; exit 0; else echo "E9 FAIL"; exit 1; fi