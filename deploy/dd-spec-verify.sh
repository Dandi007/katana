#!/usr/bin/env bash
# D1–D7 + D7.1 逐项核验 —— 确定性 ops 工装（本卷自治范围，非产品代码）。
# 用法：deploy/dd-spec-verify.sh <candidate-worktree> | --self-test
# 每项输出：判定 / 文件 / 行号 / 所跑命令 / 原始回显
set -uo pipefail


# grep -c 无命中时**既打印 0 又返回 1**。写成 `$(grep -c ... || echo 0)` 会得到 "0\n0"，
# 后续 [ "$n" -eq 0 ] 直接 integer expression expected，判定翻成 FAIL。
# 这个坑在 dd-stall-probe.sh 里已经踩过一次，这里是第二次 —— 故统一收进 helper。
count()  { local n; n=$(grep -c  "$1" "$2" 2>/dev/null); echo "${n:-0}"; }
countr() { local n; n=$(grep -rc "$1" "${@:2}" 2>/dev/null | awk -F: '{s+=$2} END{print s+0}'); echo "${n:-0}"; }
countE() { local n; n=$(grep -cE "$1" "$2" 2>/dev/null); echo "${n:-0}"; }

# --- 反模式守卫：让「第三次踩 grep -c」被机器逮到，而不是靠记性 ------------
# helper 只是**约定**：它不阻止任何人（包括我）在新脚本里重新写一遍
# `$(grep -c ... || echo 0)`。约定挡不住复发，只有判据挡得住。
# 故 --self-test 里加两条：① helper 行为回归；② 扫 deploy/*.sh 的危险惯用法。
# 局限（明说）：它只认这一个具体惯用法，换个写法仍可能算错——它把**已知这次**
# 变成机检，不等于把「计数出错」这一整类消灭。
self_test() {
  local here fail=0 f tmp n tdir
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  echo "=== ① helper 回归：无命中时必须回显恰好一个 0 ==="
  # 用独立目录，不要拿 mktemp 的 dirname（那是共享 /tmp，countr 会扫到别人的文件，
  # 自证会随环境飘——第一次注入测试就飘出 countr=[2] 的假失败）
  local tdir; tdir=$(mktemp -d); tmp="$tdir/probe.txt"; echo "nothing here" > "$tmp"
  for fn in count countE; do
    n=$($fn 'zzz-no-such-token' "$tmp")
    if [ "$(printf '%s' "$n" | wc -c)" = "1" ] && [ "$n" = "0" ]; then
      echo "  ✅ $fn 回显 [0]（1 字节）"
    else
      echo "  ❌ $fn 回显 [$n]（$(printf '%s' "$n" | wc -c) 字节）—— grep -c 陷阱回归"; fail=1
    fi
  done
  n=$(countr 'zzz-no-such-token' "$tdir")
  [ "$n" = "0" ] && echo "  ✅ countr 回显 [0]" || { echo "  ❌ countr 回显 [$n]"; fail=1; }
  rm -rf "$tdir"

  echo "=== 2 反模式扫描：deploy/*.sh 不得出现 计数 grep 直接 || echo 的写法 ==="
  # 模式用变量拼，否则扫描器会匹配到自己这一行（第一次跑就撞上了）
  local pat hits
  pat='grep -[a-zA-Z]*c[a-zA-Z]*[^|]*\|\| *echo'
  hits=$(grep -nE "$pat" "$here"/*.sh 2>/dev/null | grep -vE ':[0-9]+: *#' | grep -v 'ANTIPAT-SCANNER') || true
  if [ -z "$hits" ]; then
    echo "  ✅ 无危险惯用法（注释内的说明不算）"
  else
    echo "  ❌ 发现危险惯用法："; echo "$hits" | sed 's/^/     /'; fail=1
  fi

  echo "=== 自证结论 ==="
  [ "$fail" -eq 0 ] && { echo "  ✅ 两条均通过"; return 0; } || { echo "  ❌ 失败"; return 1; }
}

[ "${1:-}" = "--self-test" ] && { self_test; exit $?; }

ROOT="${1:?用法: $0 <candidate-worktree> | --self-test}"
cd "$ROOT" || exit 1
pass=0; fail=0


hdr() { printf '\n──────── %s ────────\n' "$*"; }
run() { printf '  $ %s\n' "$*"; eval "$@" 2>&1 | sed 's/^/    /'; }
judge() { if [ "$1" = "0" ]; then printf '  ✅ PASS %s\n' "$2"; pass=$((pass+1)); else printf '  ❌ FAIL %s\n' "$2"; fail=$((fail+1)); fi; }

hdr "D3/D4 读路径改走 DomainSearch —— vault_search 计数应为 0，katana_search 命中应 >0"
for f in mcp/wiki/katana_wiki_mcp/server.py mcp/work-folder/katana_work_folder_mcp/server.py; do
  vs=$(count 'vault_search' "$f")
  ks=$(count 'katana_search\|DomainSearch' "$f")
  printf '  %s\n' "$f"
  run "grep -n 'vault_search' $f | head -5"
  run "grep -n 'katana_search\|DomainSearch' $f | head -5"
  printf '    vault_search 计数=%s（应为 0）  katana_search/DomainSearch 计数=%s（应 >0）\n' "$vs" "$ks"
  [ "$vs" -eq 0 ] && [ "$ks" -gt 0 ]; judge $? "$f 读路径已换轨"
done
run "grep -rn 'server\.vault_search' mcp/wiki/tests mcp/work-folder/tests | head -5"
[ "$(countr 'server\.vault_search' mcp/wiki/tests mcp/work-folder/tests)" -eq 0 ]; judge $? "两域测试零 server.vault_search 桩"

hdr "D1 索引簿记：vectors_complete 列 + needs_reindex 语义"
run "grep -n 'vectors_complete' mcp/search/katana_search/index.py | head -8"
run "grep -n 'def needs_reindex' -A 8 mcp/search/katana_search/index.py"
run "grep -n 'want_vectors\|docs_missing_vectors' mcp/search/katana_search/api.py mcp/search/katana_search/index.py | head -8"
grep -q 'vectors_complete' mcp/search/katana_search/index.py && grep -q 'want_vectors' mcp/search/katana_search/api.py; judge $? "D1 向量面缺失可恢复"

hdr "D2 全量回填入口 katana_search.backfill"
run "ls -l mcp/search/katana_search/backfill.py"
run "grep -n 'ls-files\|--force\|--json' mcp/search/katana_search/backfill.py | head -6"
test -f mcp/search/katana_search/backfill.py && grep -q 'ls-files' mcp/search/katana_search/backfill.py; judge $? "D2 backfill 存在且走 git ls-files"

hdr "D5 post-commit 索引钩子"
for f in mcp/wiki/katana_wiki_mcp/search_hook.py mcp/work-folder/katana_work_folder_mcp/search_hook.py; do
  run "ls -l $f"; run "grep -n 'def after_commit' -A 3 $f"
done
run "grep -n 'search_hook\|after_commit' mcp/wiki/katana_wiki_mcp/fs_tools.py mcp/work-folder/katana_work_folder_mcp/fs_tools.py | head -8"
test -f mcp/wiki/katana_wiki_mcp/search_hook.py && test -f mcp/work-folder/katana_work_folder_mcp/search_hook.py; judge $? "D5 两域钩子模块在位"

hdr "D6 *_search_reindex 工具，且旧 wf_reindex 未被顶掉"
run "grep -n 'def wiki_search_reindex' -B 2 mcp/wiki/katana_wiki_mcp/server.py"
run "grep -n 'def wf_search_reindex' -B 2 mcp/work-folder/katana_work_folder_mcp/server.py"
run "grep -n 'def wf_reindex' -B 2 mcp/work-folder/katana_work_folder_mcp/server.py"
grep -q 'def wiki_search_reindex' mcp/wiki/katana_wiki_mcp/server.py \
  && grep -q 'def wf_search_reindex' mcp/work-folder/katana_work_folder_mcp/server.py \
  && grep -q 'def wf_reindex' mcp/work-folder/katana_work_folder_mcp/server.py; judge $? "D6 两新工具在位且旧工具保留"

hdr "D7.1 mcp/conftest.py 注入列表必须含 \"search\"（最高优先级，缺了 gate 跑不起来）"
run "grep -n 'search' mcp/conftest.py"
grep -q '"search"' mcp/conftest.py; judge $? "D7.1 conftest 已加 search"

hdr "D7.2/7.3 run-tests.sh 与 CI"
run "grep -n 'search/tests' mcp/run-tests.sh"
run "grep -n -- '-e mcp/search' .github/workflows/tests.yml"
grep -q 'search/tests' mcp/run-tests.sh && grep -q -- '-e mcp/search' .github/workflows/tests.yml; judge $? "D7 gate 接线"

hdr "先红验收：新增测试文件在位且非空壳"
for f in mcp/search/tests/test_backfill.py mcp/wiki/tests/test_search_domain.py \
         mcp/work-folder/tests/test_search_domain.py mcp/wiki/tests/test_search_hook.py \
         mcp/work-folder/tests/test_search_hook.py; do
  n=$(countE '^(async )?def test_' "$f")
  printf '  %-52s test 函数数=%s\n' "$f" "$n"
done

hdr "冻结目录零改动（mcp/kernel mcp/shared deploy docs …）"
run "git diff --name-only d455747e000c42f060b8b35bfd4ed04737dd0433 HEAD -- mcp/kernel mcp/shared mcp/memory mcp/migration mcp/remote deploy docs plugins parity"
[ -z "$(git diff --name-only d455747e000c42f060b8b35bfd4ed04737dd0433 HEAD -- mcp/kernel mcp/shared mcp/memory mcp/migration mcp/remote deploy docs plugins parity)" ]; judge $? "冻结目录未被动"

printf '\n════════ 核验汇总 PASS=%s FAIL=%s ════════\n' "$pass" "$fail"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
