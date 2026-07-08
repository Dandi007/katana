#!/usr/bin/env bash
# hook 降级路径回归：服务不可达时必须输出合法 fallback JSON，退出码 0。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
out="$(KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 bash "$HERE/../hooks/session-start")"
echo "$out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'unavailable' in d['hookSpecificOutput']['additionalContext']"
echo "PASS: fallback JSON valid"
