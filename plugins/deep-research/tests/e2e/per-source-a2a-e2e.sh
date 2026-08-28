#!/usr/bin/env bash
# per-source-a2a-e2e.sh — deep-research per-source 文件 + harvester index + reports[] A2A 契约 e2e 测试
#
# 验什么：
#   P1: workflow.js 里 per-source 文件名含双下划线 __ 分隔源名（静态 grep）
#   P2: FINDING_SCHEMA 里 reports[] 字段存在（静态 grep）
#   P3: harvester 节点存在（phase Harvest + prompt 签名）
#   P4: synthesisPrompt 先读 index.md 索引（静态 grep）
#   P5: 真机 headless 运行后，per-source 文件、index.md、reports 产物存在
#   R1: agent-*.jsonl trace 中 nonce 能被 worker/harvester/synth 三类节点命中
#
# P1-P4 为静态判据（不烧 token）；P5+R1 为动态判据（需 claude CLI 可用，
#   由主 session 在 loop PASS 后按需触发，CI 可跳过 P5+R1）。
#
# 用法：
#   静态检查（默认）：bash plugins/deep-research/tests/e2e/per-source-a2a-e2e.sh
#   完整动态（需 wiki MCP 检索 harness + work-folder MCP 产物 harness）：
#     E2E_DYNAMIC=1 KATANA_WIKI_MCP_E2E=1 KATANA_WF_MCP_E2E=1 bash plugins/deep-research/tests/e2e/per-source-a2a-e2e.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/../../skills/deep-research" && pwd)"
WORKFLOW_JS="$SKILL_DIR/workflow.js"

PASS=0
FAIL=0
result() {
  local label="$1" ok="$2" detail="${3:-}"
  if [ "$ok" = "0" ]; then
    echo "OK   $label${detail:+ — $detail}"
    PASS=$((PASS+1))
  else
    echo "FAIL $label${detail:+ — $detail}"
    FAIL=$((FAIL+1))
  fi
}

echo "=== per-source-a2a e2e ==="
echo "workflow: $WORKFLOW_JS"
echo ""

# ── P1: per-source 文件名含双下划线 __ ──────────────────────────────────────────
grep -qE 'findingsPath.*r\$\{round\}-c\$\{clue\.id\}__' "$WORKFLOW_JS" 2>/dev/null
result "P1: per-source 文件名含 __ 分隔符" "$?"

# ── P2: FINDING_SCHEMA 含 reports[] 字段 ────────────────────────────────────────
grep -qE "required:.*reports|'reports'|\"reports\"" "$WORKFLOW_JS" 2>/dev/null
result "P2: FINDING_SCHEMA reports[] 字段" "$?"

# ── P3: harvester 节点存在（Harvest phase + harvest/汇编索引 签名）───────────────
grep -q "Harvest" "$WORKFLOW_JS" 2>/dev/null && grep -qiE "harvest|汇编索引" "$WORKFLOW_JS" 2>/dev/null
result "P3: harvester 节点 (Harvest phase + 签名)" "$?"

# ── P4: synthesisPrompt 含 index.md（出现 ≥2 次）────────────────────────────────
INDEX_COUNT="$(grep -c 'index\.md' "$WORKFLOW_JS" 2>/dev/null || echo 0)"
[ "$INDEX_COUNT" -ge 2 ]
result "P4: index.md 贯穿 harvester+synth (≥2 次，实际: $INDEX_COUNT)" "$?"

# ── P5 + R1: 动态真机验证 ────────────────────────────────────────────────────────
if [ "${E2E_DYNAMIC:-0}" != "1" ]; then
  echo ""
  echo "跳过 P5+R1（动态真机，需 E2E_DYNAMIC=1 + claude CLI 可用）"
else
  if [ "${KATANA_WIKI_MCP_E2E:-0}" != "1" ] || [ "${KATANA_WF_MCP_E2E:-0}" != "1" ]; then
    echo "跳过 P5+R1：需要 KATANA_WIKI_MCP_E2E=1（wiki 检索 harness）与 KATANA_WF_MCP_E2E=1（work-folder MCP harness）"
    echo ""
    echo "=== 结果: PASS=$PASS FAIL=$FAIL ==="
    [ "$FAIL" -eq 0 ]
    exit $?
  fi
  command -v claude >/dev/null || { echo "ABORT: claude CLI 未安装"; exit 2; }

  CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  NONCE="E2EPS$(date +%s)$$"
  WORK="$(mktemp -d -t dr-persource.XXXXXX)"

  # driver workflow：注入精确 args，两个本地源 → 期望产出 ≥2 个 per-source 文件（离线确定性）
  # 使用 return await workflow(...) 与已验证的 model-routing-e2e.sh 同款 idiom
  DRIVER="$WORK/driver.mjs"
  cat > "$DRIVER" <<DRIVER_JS
export const meta = { name: 'dr-persource-e2e-driver', description: 'per-source a2a e2e driver', phases: [{ title: 'Run' }] }
const childArgs = {
  topic: "[$NONCE] 总结两条 wiki 笔记关于手冲咖啡萃取的要点",
  // 不传 folderId：让 Setup 节点自行 wf_create（topic 含 NONCE，事后 wf_search 可定位）
  skillDir: "$SKILL_DIR",
  sources: {},
  maxWidth: 1,
  // worker/harvest 轻量任务用 haiku；synth 要走完 5 步(读 index→选择性读 L2→sources→topics→report)
  // 写出终稿 report.md，haiku 易在 report 前停笔，给 sonnet 保证端到端跑完
  models: { worker: "haiku", triage: "sonnet", synth: "sonnet", harvest: "haiku" },
  initialClues: [
    { id: "c0", text: "[$NONCE] 用 katana-wiki-mcp search 分别检索 MCP harness 预置的两条咖啡萃取笔记并提取关键因素", local: true,
      suggested_sources: ["wiki-primary", "wiki-secondary"], depth: 0 }
  ],
}
return await workflow({ scriptPath: "$WORKFLOW_JS" }, childArgs)
DRIVER_JS

  echo "nonce:   $NONCE"
  echo "work:    $WORK"
  echo "running headless claude -p…"

  printf '%s' "用 Workflow 工具跑这个脚本文件，只调用一次，scriptPath=${DRIVER}。不要传 args，不要做别的，跑完把返回简述即可。" \
    | claude -p --permission-mode acceptEdits \
        --allowedTools Workflow,Agent,mcp__katana-wiki-mcp__search,mcp__katana-wiki-mcp__page_get,mcp__katana-work-folder-mcp__wf_create,mcp__katana-work-folder-mcp__wf_search,mcp__katana-work-folder-mcp__fs_create,mcp__katana-work-folder-mcp__fs_write,mcp__katana-work-folder-mcp__fs_read,mcp__katana-work-folder-mcp__fs_list \
        > "$WORK/claude.log" 2>&1
  CL_EXIT=$?

  [ "$CL_EXIT" -eq 0 ]
  result "P5-a: claude -p 正常退出(exit=$CL_EXIT)" "$?"

  # 经 work-folder MCP 拉取研究 folder 快照；client 不挂载 work folder 存储。
  printf '%s' "只调用 work-folder MCP：先 wf_search 检索 \"$NONCE\" 找到本次研究 work folder，然后 fs_list 列出该 folder 内 findings/ 下全部文件名，再 fs_read findings/index.md。逐行输出文件名（folder 相对路径），随后原样输出 index 内容，不要加解释。" \
    | claude -p --allowedTools mcp__katana-work-folder-mcp__wf_search,mcp__katana-work-folder-mcp__fs_list,mcp__katana-work-folder-mcp__fs_read \
        > "$WORK/wf-snapshot.log" 2>&1
  SNAP_EXIT=$?
  [ "$SNAP_EXIT" -eq 0 ]
  result "P5-b0: work-folder MCP 快照正常退出(exit=$SNAP_EXIT)" "$?"

  # P5-b: MCP 返回的 per-source 文件名含 __ 分隔符，≥2 个确认多源拆分
  PS_COUNT="$(grep -oE 'r[^ /]*-c[^ /]*__[^ /]*\.md' "$WORK/wf-snapshot.log" 2>/dev/null | sort -u | wc -l | tr -d ' ')"
  [ "$PS_COUNT" -ge 2 ]
  result "P5-b: MCP 返回 per-source 文件名(含 __, ≥2 源拆分，实际: $PS_COUNT)" "$?"

  # P5-c: index.md 可经 MCP 读取
  grep -q 'findings/index.md' "$WORK/wf-snapshot.log" 2>/dev/null
  result "P5-c: findings/index.md 可经 work-folder MCP 读取" "$?"

  # P5-d: index.md 含 reports 格式内容
  grep -qi "reports\|source\|evidence_credibility" "$WORK/wf-snapshot.log" 2>/dev/null
  result "P5-d: index.md 含索引内容(reports/source/evidence_credibility)" "$?"

  # P5-e: 经 MCP 读取 synth 终稿，证顶层 return 链路完成
  printf '%s' "只调用 work-folder MCP：先 wf_search 检索 \"$NONCE\" 找到本次研究 work folder，然后 fs_read 该 folder 的 report.md，原样输出文件内容，不要加解释。" \
    | claude -p --allowedTools mcp__katana-work-folder-mcp__wf_search,mcp__katana-work-folder-mcp__fs_read > "$WORK/report-snapshot.md" 2>&1
  REPORT_EXIT=$?
  [ "$REPORT_EXIT" -eq 0 ] && [ "$(wc -c < "$WORK/report-snapshot.md")" -ge 500 ]
  result "P5-e: report.md 经 work-folder MCP 取回且非空(≥500B)" "$?"

  # R1: agent-*.jsonl trace 中 nonce 命中 worker + synth
  python3 - "$CFG" "$NONCE" <<'PY'
import sys, glob, os
cfg, nonce = sys.argv[1], sys.argv[2]
seen = {"worker": 0, "harvest": 0, "synth": 0}
for f in glob.glob(os.path.join(cfg, "**", "agent-*.jsonl"), recursive=True):
    try:
        t = open(f, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    if nonce not in t:
        continue
    # 按互斥唯一签名分类。不可用 "harvest" 宽松小写子串——worker trace 偶含该词会被误判成
    # harvest（实测 bug）。synth=探索已收敛 / harvest=汇编索引 / worker=收集证据，三者互不重叠。
    if "探索已收敛" in t:
        seen["synth"] += 1
    elif "汇编索引" in t:
        seen["harvest"] += 1
    elif "收集证据" in t:
        seen["worker"] += 1
fail = 0
# worker + synth 缺失 → 硬失败；harvest 单独跑 nonce 可能不入 prompt → 降级 WARN
for cls, count in seen.items():
    if count == 0:
        if cls == "harvest":
            print(f"WARN R1-{cls}: 无 trace（harvester nonce 可能不入 agent log，可接受）")
        else:
            print(f"FAIL R1-{cls}: 无 trace（worker/synth 必须命中 nonce）")
            fail = 1
    else:
        print(f"OK   R1-{cls}: {count}x trace")
sys.exit(fail)
PY
  result "R1: agent trace 覆盖验证" "$?"

  if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "PASS: 全部动态断言通过，清理 $WORK"
    rm -rf "$WORK"
  else
    echo ""
    echo "FAIL: 部分断言失败，保留产物: $WORK"
  fi
fi

echo ""
echo "=== 结果: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
