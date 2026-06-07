#!/usr/bin/env bash
# tests/run-contracts.sh — 入口薄壳：环境检查后转交 runner.py
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
command -v uv >/dev/null || { echo "ABORT: uv not installed"; exit 1; }
command -v claude >/dev/null || { echo "ABORT: claude CLI not installed"; exit 1; }
exec uv run "$HERE/runner.py" "$@"
