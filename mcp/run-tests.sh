#!/usr/bin/env bash
# mcp server 测试 gate：shared(kernel) + wiki + work-folder + memory 四包的 L0 单元 +
# 集成回归 + 治理 Full VFS/policy parity 测试（进程退出码即结果，design §9.1）。
# 用 --import-mode=importlib 避开四包同名 `tests` 包的 collection 冲突。
# 用法：PYTHON=/path/to/venv/bin/python bash mcp/run-tests.sh [pytest 额外参数]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
exec "$PY" -m pytest \
  "$HERE/shared/tests" "$HERE/wiki/tests" "$HERE/work-folder/tests" "$HERE/memory/tests" \
  --import-mode=importlib -p no:cacheprovider "$@"
