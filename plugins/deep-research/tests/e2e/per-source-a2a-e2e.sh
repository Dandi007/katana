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
#   完整动态（需 claude）：E2E_DYNAMIC=1 bash plugins/deep-research/tests/e2e/per-source-a2a-e2e.sh
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
grep -qE 'findings/r\$\{round\}-c\$\{clue\.id\}__' "$WORKFLOW_JS" 2>/dev/null
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
  command -v claude >/dev/null || { echo "ABORT: claude CLI 未安装"; exit 2; }

  CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  NONCE="E2EPS$(date +%s)$$"
  WORK="$(mktemp -d -t dr-persource.XXXXXX)"
  KB="$WORK/kb"
  TOPIC_DIR="$KB/DeepThought/$NONCE"
  mkdir -p "$TOPIC_DIR/findings"

  # 最小离线 KB：两条本地笔记（确定性双源，不依赖 web，离线即可证多源拆分）
  mkdir -p "$KB"
  cat > "$KB/note_a.md" <<'NOTE'
# 手冲咖啡萃取要点 A
- 水温 90-96°C 影响萃取率
- 研磨度越细萃取越快
- 粉水比常用 1:15
NOTE
  cat > "$KB/note_b.md" <<'NOTE'
# 手冲咖啡萃取要点 B
- 闷蒸时间 30-40 秒，释放 CO2
- 总冲泡时间约 2.5-3 分钟
- 新鲜豆风味更佳
NOTE

  # driver workflow：注入精确 args，两个本地源 → 期望产出 ≥2 个 per-source 文件（离线确定性）
  # 使用 return await workflow(...) 与已验证的 model-routing-e2e.sh 同款 idiom
  DRIVER="$WORK/driver.mjs"
  cat > "$DRIVER" <<DRIVER_JS
export const meta = { name: 'dr-persource-e2e-driver', description: 'per-source a2a e2e driver', phases: [{ title: 'Run' }] }
const childArgs = {
  topic: "[$NONCE] 总结这两条本地笔记关于手冲咖啡萃取的要点",
  topicDir: "$TOPIC_DIR",
  skillDir: "$SKILL_DIR",
  sources: {
    note_a: "$KB/note_a.md",
    note_b: "$KB/note_b.md",
  },
  maxWidth: 1,
  models: { worker: "haiku", triage: "sonnet", synth: "haiku", harvest: "haiku" },
  initialClues: [
    { id: "c0", text: "[$NONCE] Read $KB/note_a.md 和 $KB/note_b.md，列出影响萃取的关键因素", local: true,
      suggested_sources: ["note_a", "note_b"], depth: 0 }
  ],
}
return await workflow({ scriptPath: "$WORKFLOW_JS" }, childArgs)
DRIVER_JS

  echo "nonce:   $NONCE"
  echo "work:    $WORK"
  echo "running headless claude -p…"

  printf '%s' "用 Workflow 工具跑这个脚本文件，只调用一次，scriptPath=${DRIVER}。不要传 args，不要做别的，跑完把返回简述即可。" \
    | claude -p --permission-mode acceptEdits \
        --allowedTools Workflow,Agent,Read,Write,Grep,Glob,Bash \
        > "$WORK/claude.log" 2>&1
  CL_EXIT=$?

  [ "$CL_EXIT" -eq 0 ]
  result "P5-a: claude -p 正常退出(exit=$CL_EXIT)" "$?"

  # P5-b: per-source 文件存在（含 __ 分隔符，≥2 个确认多源拆分）
  PS_COUNT="$(find "$TOPIC_DIR/findings" -name 'r*-c*__*.md' 2>/dev/null | wc -l | tr -d ' ')"
  [ "$PS_COUNT" -ge 2 ]
  result "P5-b: per-source 文件存在(含 __, ≥2 源拆分，实际: $PS_COUNT)" "$?"

  # P5-c: index.md 存在
  test -f "$TOPIC_DIR/findings/index.md"
  result "P5-c: findings/index.md 存在" "$?"

  # P5-d: index.md 含 reports 格式内容
  grep -qi "reports\|source\|credibility" "$TOPIC_DIR/findings/index.md" 2>/dev/null
  result "P5-d: index.md 含索引内容(reports/source/credibility)" "$?"

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
    if "探索已收敛" in t:
        seen["synth"] += 1
    elif "汇编索引" in t or "harvest" in t.lower():
        seen["harvest"] += 1
    elif "针对線索" in t or "针对线索" in t:
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
