#!/usr/bin/env bash
# 验证包进程隔离保留所有七包与失败汇总，不吞掉前包失败。
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT
cat > "$SCRATCH/python" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$3" >> "$MCP_TEST_LOG"
[[ "${MCP_TEST_FAIL:-}" != memory || "$3" != */memory/tests ]]
SH
chmod +x "$SCRATCH/python"
export MCP_TEST_LOG="$SCRATCH/calls"
PYTHON="$SCRATCH/python" bash "$REPO/mcp/run-tests.sh" -q
[[ "$(wc -l < "$MCP_TEST_LOG")" -eq 7 ]]
: > "$MCP_TEST_LOG"
if MCP_TEST_FAIL=memory PYTHON="$SCRATCH/python" bash "$REPO/mcp/run-tests.sh" -q; then
  echo '错误：失败包未传递非零退出码' >&2
  exit 1
fi
[[ "$(wc -l < "$MCP_TEST_LOG")" -eq 7 ]]
grep -q '/remote/tests$' "$MCP_TEST_LOG"
echo 'PASS: 七包独立执行，失败汇总且继续覆盖后续包'
