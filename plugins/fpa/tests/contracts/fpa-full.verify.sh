#!/usr/bin/env bash
# fpa-full.verify.sh — FPA 三件套机械校验
#
# 路径回溯说明：
#   $0 = <repo>/plugins/fpa/tests/contracts/fpa-full.verify.sh
#   PLUGIN_DIR = <repo>/plugins/fpa   （向上两级）
#   validate_fpa.py 在 PLUGIN_DIR/skills/fpa/scripts/validate_fpa.py
#
# normalize 说明：
#   runner 的 verdict.inputs 不支持 glob，verify.sh 末尾把 FPA-*.md cp 到
#   FPA-LATEST.md，契约的 verdict.inputs 指向该固定名。
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
VALIDATE="$PLUGIN_DIR/skills/fpa/scripts/validate_fpa.py"
FPA_DIR="$KB_DIR/docs/fpa"

# ── 1. validate_fpa.py 存在 ─────────────────────────────────────────────────
if [ ! -f "$VALIDATE" ]; then
  echo "FAIL: validate_fpa.py 不存在: $VALIDATE"
  exit 1
fi

# ── 2. FPA-*.md 存在且唯一 ──────────────────────────────────────────────────
FPA_FILES=("$FPA_DIR"/FPA-*.md)
if [ ${#FPA_FILES[@]} -eq 0 ] || [ ! -f "${FPA_FILES[0]}" ]; then
  echo "FAIL: $FPA_DIR 中找不到 FPA-*.md"
  exit 1
fi
FPA_FILE="${FPA_FILES[0]}"

# ── 3. RUN-REPORT-*.md 存在且与 FPA slug 一致 ───────────────────────────────
FPA_BASENAME="$(basename "$FPA_FILE")"
SLUG="${FPA_BASENAME#FPA-}"
SLUG="${SLUG%.md}"
RUN_REPORT="$FPA_DIR/RUN-REPORT-${SLUG}.md"
if [ ! -f "$RUN_REPORT" ]; then
  echo "FAIL: RUN-REPORT-${SLUG}.md 不存在（slug 不一致或 Phase 5 未完成）"
  exit 1
fi
echo "slug=$SLUG  ✓"

# ── 4. validate_fpa.py 对 FPA 文档做结构校验 ─────────────────────────────────
echo "运行 validate_fpa.py $FPA_BASENAME ..."
python3 "$VALIDATE" "$FPA_FILE"
echo "validate_fpa.py PASS ✓"

# ── 5. adversarial-verdicts.json 是合法 JSON 且含 target/verdict 字段 ────────
VERDICTS="$FPA_DIR/adversarial-verdicts.json"
python3 - "$VERDICTS" <<'PYEOF'
import json, sys
path = sys.argv[1]
try:
    data = json.load(open(path, encoding="utf-8"))
except (OSError, json.JSONDecodeError) as e:
    print(f"FAIL: adversarial-verdicts.json 解析失败: {e}")
    sys.exit(1)
items = data.get("verdicts") if isinstance(data, dict) else data
if not isinstance(items, list) or not items:
    print("FAIL: adversarial-verdicts.json 必须是数组（或含 verdicts 字段的对象）且非空")
    sys.exit(1)
missing = [i for i, v in enumerate(items) if not (isinstance(v, dict) and "target" in v and "verdict" in v)]
if missing:
    print(f"FAIL: verdicts[{missing}] 缺少 target 或 verdict 字段")
    sys.exit(1)
print(f"adversarial-verdicts.json PASS ✓  ({len(items)} verdicts)")
PYEOF

# ── 6. normalize：cp FPA 文档到固定名供 verdict.inputs 使用 ─────────────────
cp "$FPA_FILE" "$FPA_DIR/FPA-LATEST.md"
echo "normalized → FPA-LATEST.md ✓"

echo "ALL CHECKS PASS"
