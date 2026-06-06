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

# 3) 契约 schema 全量校验
uv run "$HERE/runner.py" --validate-only --repo "$REPO" || err "contract schema validation failed"

# 4) plugin.json ↔ marketplace.json 一致性
python3 - "$REPO" <<'PY' || FAIL=1
import json, sys, pathlib
repo = pathlib.Path(sys.argv[1])
mkt = {p["name"] for p in json.loads((repo/".claude-plugin/marketplace.json").read_text())["plugins"]}
disk = {p.parent.parent.name for p in repo.glob("plugins/*/.claude-plugin/plugin.json")}
if mkt != disk:
    print(f"G0-FAIL: marketplace {sorted(mkt)} != disk {sorted(disk)}"); sys.exit(1)
PY

# 5) --ci 模式：PR 必须带本机 sweep 报告，且报告 commit 之后无实质改动
if [ "${1:-}" = "--ci" ]; then
  R=$(ls -t "$HERE/reports/"*.md 2>/dev/null | head -1)
  [ -n "$R" ] || err "no sweep report in tests/reports/"
  if [ -n "$R" ]; then
    SHA=$(basename "$R" .md | sed 's/.*-//')
    git -C "$REPO" cat-file -e "$SHA" 2>/dev/null || err "report sha $SHA not in history"
    CHANGED=$(git -C "$REPO" diff --name-only "$SHA" HEAD -- ':!tests/reports' 2>/dev/null)
    [ -z "$CHANGED" ] || err "substantive changes after report $SHA: $CHANGED"
  fi
fi

[ "$FAIL" -eq 0 ] && echo "G0 PASS"
exit "$FAIL"
