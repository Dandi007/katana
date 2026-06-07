#!/usr/bin/env bash
# deep-research-kb.verify.sh — deep-research 产物机械校验
#
# SKILL.md 规定报告落盘于 $KB/DeepThought/<主题名>/report.md，主题名由模型生成（含空格）。
# verify.sh 负责：
#   1. 找到 DeepThought/*/report.md
#   2. 校验引用结构与 92/96 内容
#   3. 校验最小体积（≥2000B）
#   4. normalize：cp 到 $KB_DIR/research-report.md，供 verdict.inputs 引用（固定路径）
set -euo pipefail

# ── 1. 找报告文件 ────────────────────────────────────────────────────────────
REPORT_FILE=""
while IFS= read -r -d '' f; do
  REPORT_FILE="$f"
  break
done < <(find "$KB_DIR/DeepThought" -maxdepth 2 -name "report.md" -print0 2>/dev/null)

if [ -z "$REPORT_FILE" ]; then
  echo "FAIL: $KB_DIR/DeepThought/*/report.md 不存在（Workflow 未完成或路径错误）"
  exit 1
fi
echo "found: $REPORT_FILE"

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

# ── 5. normalize → 固定路径供 verdict.inputs 使用 ────────────────────────────
cp "$REPORT_FILE" "$KB_DIR/research-report.md"
echo "normalized → research-report.md ✓"

echo "ALL CHECKS PASS"
