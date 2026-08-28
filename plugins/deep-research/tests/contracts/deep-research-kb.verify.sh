#!/usr/bin/env bash
# deep-research-kb.verify.sh — deep-research 产物机械校验
#
# 报告位于研究 work folder（topic = deep-research: katana-e2e-coffee）的 report.md，
# 经 work-folder MCP 读取（client 不挂载 work folder 存储）。
# verify.sh 负责：
#   1. 通过 work-folder MCP wf_search + fs_read 取回 report.md
#   2. 校验引用结构与 92/96 内容
#   3. 校验最小体积（≥2000B）
#   4. normalize 到 $CWD/research-report.md，供 verdict.inputs 引用（固定路径）
set -euo pipefail

# ── 1. 经 work-folder MCP 读取报告 ──────────────────────────────────────────
command -v claude >/dev/null || { echo "FAIL: claude CLI 不可用，无法调用 work-folder MCP"; exit 1; }
REPORT_FILE="$CWD/research-report.md"
printf '%s' "用 work-folder MCP wf_search 检索 'deep-research: katana-e2e-coffee' 找到研究 work folder，然后 fs_read 该 folder 的 report.md，原样输出文件内容，不要代码围栏或解释。" \
  | claude -p --allowedTools mcp__katana-work-folder-mcp__wf_search,mcp__katana-work-folder-mcp__fs_read > "$REPORT_FILE"

if [ ! -s "$REPORT_FILE" ]; then
  echo "FAIL: work-folder MCP 未返回 deep-research: katana-e2e-coffee 的 report.md"
  exit 1
fi
echo "retrieved via work-folder MCP: deep-research: katana-e2e-coffee / report.md"

# ── 2. 最小体积 2000B ────────────────────────────────────────────────────────
SIZE=$(wc -c < "$REPORT_FILE")
if [ "$SIZE" -lt 2000 ]; then
  echo "FAIL: report.md 体积 ${SIZE}B < 2000B，内容不足"
  exit 1
fi
echo "size=${SIZE}B ✓"

# ── 3. 含 92 与 96（矛盾对绕不开）──────────────────────────────────────────
if ! grep -qE "92" "$REPORT_FILE"; then
  echo "FAIL: report.md 不含 '92'（意式浓缩温度矛盾对缺失）"
  exit 1
fi
if ! grep -qE "96" "$REPORT_FILE"; then
  echo "FAIL: report.md 不含 '96'（意式浓缩温度矛盾对缺失）"
  exit 1
fi
echo "92/96 present ✓"

# ── 4. 含引用结构（[[...]] / References / 来源）────────────────────────────
if ! grep -qE '\[\[.*\]\]|References|来源' "$REPORT_FILE"; then
  echo "FAIL: report.md 缺少引用结构（无 [[...]] / References / 来源）"
  exit 1
fi
echo "citation structure present ✓"

echo "ALL CHECKS PASS"
