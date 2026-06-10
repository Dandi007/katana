#!/usr/bin/env bash
# model-routing-e2e.sh — deep-research 三档 agent 模型路由的 CLI 端到端测试
#
# 为什么不走 tests/run-contracts.sh 契约 harness：
#   契约 harness 把 claude 固定到 ccs/lingzhi 网关（ANTHROPIC_BASE_URL=127.0.0.1:15721，
#   model=lingzhi/claude-opus-4-8），而该网关 group 下**只有 opus 一个可用渠道**——
#   haiku/sonnet 一律 503「无可用渠道」。任何 per-agent 模型 override（别名下发时被剥掉
#   provider 前缀）都会熔断。故三档路由无法在该网关下验证，必须用**默认鉴权**直跑。
#
# 验什么：分支真身 workflow.js 读 args.models → WORKER/TRIAGE/SYNTH_MODEL → 三处 agent({model})。
#   driver 用 workflow({scriptPath}) 把精确 args 注入真身 workflow.js（不让模型手抄 JSON）。
#   断言：worker 跑 haiku、triage 跑 sonnet、synth 跑 opus（trace 里 "model" 字段为准）。
#
# 前置：默认鉴权 claude 能路由 haiku/sonnet/opus（非 ccs-pinned 环境）。
# 用法：bash plugins/deep-research/tests/e2e/model-routing-e2e.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/../../skills/deep-research" && pwd)"
WORKFLOW_JS="$SKILL_DIR/workflow.js"
[ -f "$WORKFLOW_JS" ] || { echo "ABORT: 找不到 workflow.js: $WORKFLOW_JS"; exit 2; }
command -v claude >/dev/null || { echo "ABORT: claude CLI 未安装"; exit 2; }

CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
NONCE="E2EMR$(date +%s)$$"
WORK="$(mktemp -d -t dr-modelroute.XXXXXX)"
KB="$WORK/kb"
TOPIC_DIR="$KB/DeepThought/$NONCE"
mkdir -p "$TOPIC_DIR/findings"

# ── 最小 KB：一条本地笔记，worker 只需 Read 它即可完成 ──────────────────────────
cat > "$KB/note.md" <<'NOTE'
# 手冲咖啡萃取要点
- 水温 90-96°C 影响萃取率
- 研磨度越细萃取越快
- 粉水比常用 1:15
NOTE

# ── driver workflow：硬编码 childArgs，调用真身 workflow.js（嵌套仅一层，合法）────
DRIVER="$WORK/driver.mjs"
cat > "$DRIVER" <<JS
export const meta = { name: 'dr-modelroute-e2e-driver', description: '注入精确 args 跑真身 deep-research workflow.js 验三档路由', phases: [{ title: 'Run' }] }
const childArgs = {
  topic: "[$NONCE] 总结这条本地笔记里影响手冲咖啡萃取的要点",
  topicDir: "$TOPIC_DIR",
  skillDir: "$SKILL_DIR",
  sources: {},
  maxWidth: 1,
  models: { worker: "haiku", triage: "sonnet", synth: "opus" },
  initialClues: [
    { id: "c0", text: "[$NONCE] Read 文件 $KB/note.md，列出其中影响萃取的 3 个因素", local: true, suggested_sources: ["$KB"], depth: 0 }
  ],
}
return await workflow({ scriptPath: "$WORKFLOW_JS" }, childArgs)
JS

echo "=== model-routing e2e ==="
echo "nonce:     $NONCE"
echo "workflow:  $WORKFLOW_JS"
echo "work dir:  $WORK"
echo "running headless claude -p (default auth)…"

# ── 默认鉴权跑 driver（prompt 走 stdin，规避 --allowedTools 吞位置参数的坑）────────
printf '%s' "用 Workflow 工具跑这个脚本文件，只调用一次，scriptPath=${DRIVER} 。不要传 args，不要做别的，跑完把返回简述即可。" \
  | claude -p --permission-mode acceptEdits \
      --allowedTools Workflow,Agent,Read,Write,Grep,Glob,Bash \
      > "$WORK/claude.log" 2>&1
CL_EXIT=$?
echo "claude -p exit: $CL_EXIT"
[ "$CL_EXIT" -eq 0 ] || { echo "FAIL: claude -p 非零退出"; tail -20 "$WORK/claude.log"; exit 1; }

# ── 用 nonce 精确锁定本次 run 的 agent traces，分类断言 ───────────────────────────
python3 - "$CFG" "$NONCE" <<'PY'
import sys, glob, os, re
cfg, nonce = sys.argv[1], sys.argv[2]
EXP = {"worker": "claude-haiku-4-5", "triage": "claude-sonnet-4-6", "synth": "claude-opus-4-8"}
seen = {"worker": [], "triage": [], "synth": []}
for f in glob.glob(os.path.join(cfg, "**", "agent-*.jsonl"), recursive=True):
    try:
        t = open(f, encoding="utf-8", errors="ignore").read()
    except OSError:
        continue
    if nonce not in t:
        continue
    if "探索已收敛" in t:
        cls = "synth"
    elif "triage 判断脑" in t:
        cls = "triage"
    elif "针对线索" in t and "收集证据" in t:
        cls = "worker"
    else:
        continue
    m = re.search(r'"model":"([^"]*)"', t)
    seen[cls].append(m.group(1) if m else "NONE")

fail = 0
for cls in ("worker", "triage", "synth"):
    models, exp = seen[cls], EXP[cls]
    if not models:
        print(f"FAIL {cls}: 无 trace（workflow 未跑到该阶段？）"); fail = 1; continue
    bad = [m for m in models if exp not in m]
    if bad:
        print(f"FAIL {cls}: expected *{exp}* got {sorted(set(models))}"); fail = 1
    else:
        print(f"OK   {cls}: {len(models)}x {sorted(set(models))}")
sys.exit(fail)
PY
RC=$?
if [ "$RC" -eq 0 ]; then
  echo "PASS: 三档模型路由端到端生效"
  rm -rf "$WORK"
else
  echo "FAIL: 模型路由断言未通过（保留产物便于排查）: $WORK"
fi
exit $RC
