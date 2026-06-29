#!/usr/bin/env bash
# tests/lint-structure.sh — G0：结构 lint + 契约覆盖 diff（+ --ci 模式校验报告）
# bash 3.2 兼容：不用 globstar / mapfile。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
FAIL=0
err(){ echo "G0-FAIL: $1"; FAIL=1; }

# 1) 每个 skill 目录必须有合法 frontmatter 的 SKILL.md（绕 while|pipe 子 shell：先收集再判断）
FRONT_BAD="$(find "$REPO/plugins" -path "*/skills/*/SKILL.md" -not -path "*/target/*" \
  -exec sh -c 'head -1 "$1" | grep -q "^---$" || echo "$1"' _ {} \;)"
[ -z "$FRONT_BAD" ] || err "no frontmatter: $FRONT_BAD"

# 2) 契约覆盖 diff：每个 skill 要么有契约，要么在豁免清单
SKILLS=$(find "$REPO/plugins" -path "*/skills/*/SKILL.md" -not -path "*/target/*" \
  | sed -E 's|.*/plugins/([^/]+)/skills/([^/]+)/SKILL.md|\1:\2|' | sort)
COVERED=$(find "$REPO/plugins" -path "*/tests/contracts/*.contract.yaml" 2>/dev/null \
  | xargs grep -h '^skill:' 2>/dev/null | sed 's/^skill:[[:space:]]*//' | sort -u)
for s in $SKILLS; do
  echo "$COVERED" | grep -qx "$s" && continue
  grep -qE "^$s[[:space:]]" "$HERE/coverage-exemptions.txt" && continue
  err "skill $s has no contract and no exemption"
done

# 3) 契约 schema 全量校验（40 契约全 valid）
uv run "$HERE/runner.py" --validate-only --repo "$REPO" || err "contract schema validation failed"

# 3b) stdout_grep 类型 0 命中（G2 闸门）：仓内不得出现 stdout_grep assert
SG=$(grep -rn "stdout_grep" "$REPO/plugins" "$REPO/tests" 2>/dev/null \
  | grep -v "Binary" \
  | grep -v "lint-structure\.sh" \
  | grep -v "__pycache__" \
  | grep -v "\.pyc" \
  | grep -v "test_schema\.py" \
  | grep -v "asserts\.py" \
  | grep -v "tests/reports/" \
  | grep -v "\.md:" \
  || true)
[ -z "$SG" ] || err "stdout_grep found (G2): $SG"

# 3c) KB_DIR 在契约 verify/script shell 中 0 命中（旧 harness env 名，已迁 CWD）
# 只扫 tests/contracts/*.sh（harness script 逃逸口），不扫 skill 实现/文档
KD=$(find "$REPO/plugins" -path "*/tests/contracts/*.sh" \
  | xargs grep -ln "KB_DIR" 2>/dev/null || true)
[ -z "$KD" ] || err "KB_DIR found in contract scripts (use CWD instead): $KD"

# 4) plugin.json ↔ marketplace.json 一致性
python3 - "$REPO" <<'PY' || FAIL=1
import json, sys, pathlib
repo = pathlib.Path(sys.argv[1])
mkt = {p["name"] for p in json.loads((repo/".claude-plugin/marketplace.json").read_text())["plugins"]}
disk = {p.parent.parent.name for p in repo.glob("plugins/*/.claude-plugin/plugin.json")}
if mkt != disk:
    print(f"G0-FAIL: marketplace {sorted(mkt)} != disk {sorted(disk)}"); sys.exit(1)
PY

# 4b) Codex plugin wrappers ↔ marketplace.json 一致性
python3 "$REPO/scripts/validate_codex_plugins.py" || FAIL=1

# 5) table.json ↔ hooks.json 一致性校验
python3 - "$REPO" <<'PY' || FAIL=1
import json, sys, pathlib
repo = pathlib.Path(sys.argv[1])
table_path = repo / "parity/adapter/opencode/table.json"
if not table_path.exists():
    print("G0-FAIL: parity/adapter/opencode/table.json not found")
    sys.exit(1)

table = json.loads(table_path.read_text())

# Forward check: table.json entries must have corresponding hooks
for entry in table.get("sessionStart", []):
    plugin = entry["plugin"]
    hooks_json = repo / "plugins" / plugin / "hooks" / "hooks.json"
    if not hooks_json.exists():
        print(f"G0-FAIL: {plugin} listed in table.json sessionStart but {hooks_json} not found")
        sys.exit(1)

    hooks = json.loads(hooks_json.read_text())
    session_hooks = hooks.get("hooks", {}).get("SessionStart", [])
    if not session_hooks:
        print(f"G0-FAIL: {plugin} listed in table.json sessionStart but no SessionStart hooks in {hooks_json}")
        sys.exit(1)

for entry in table.get("postToolUse", []):
    plugin = entry["plugin"]
    hooks_json = repo / "plugins" / plugin / "hooks" / "hooks.json"
    if not hooks_json.exists():
        print(f"G0-FAIL: {plugin} listed in table.json postToolUse but {hooks_json} not found")
        sys.exit(1)

    hooks = json.loads(hooks_json.read_text())
    post_hooks = hooks.get("hooks", {}).get("PostToolUse", [])
    if not post_hooks:
        print(f"G0-FAIL: {plugin} listed in table.json postToolUse but no PostToolUse hooks in {hooks_json}")
        sys.exit(1)

# Reverse check: every plugin with SessionStart hooks must be in table.json
table_session_plugins = {e["plugin"] for e in table.get("sessionStart", [])}
for hooks_json in repo.glob("plugins/*/hooks/hooks.json"):
    plugin = hooks_json.parent.parent.name
    hooks = json.loads(hooks_json.read_text())
    session_hooks = hooks.get("hooks", {}).get("SessionStart", [])
    if session_hooks and plugin not in table_session_plugins:
        print(f"G0-FAIL: {plugin} has SessionStart hooks but not listed in table.json sessionStart")
        sys.exit(1)

print("table.json ↔ hooks.json consistency: OK")
PY

# 6) --ci 模式：PR 必须带本机 sweep 报告，且至少一份报告之后无实质改动。
#    不可用 ls -t 选"最新"——CI checkout 后 mtime 全相同，顺序随机；
#    语义正确的判定：任一报告的 sha..HEAD 无 tests/reports 之外的 diff 即新鲜。
if [ "${1:-}" = "--ci" ]; then
  REPORTS=$(ls "$HERE/reports/"*.md 2>/dev/null)
  [ -n "$REPORTS" ] || err "no sweep report in tests/reports/"
  if [ -n "$REPORTS" ]; then
    FRESH=""
    for R in $REPORTS; do
      SHA=$(basename "$R" .md | sed 's/.*-//')
      git -C "$REPO" cat-file -e "$SHA" 2>/dev/null || continue
      CHANGED=$(git -C "$REPO" diff --name-only "$SHA" HEAD -- ':!tests/reports' 2>/dev/null)
      [ -z "$CHANGED" ] && { FRESH="$R"; break; }
    done
    [ -n "$FRESH" ] || err "no sweep report is fresh (every report sha has substantive changes before HEAD)"
  fi
fi

[ "$FAIL" -eq 0 ] && echo "G0 PASS"
exit "$FAIL"
