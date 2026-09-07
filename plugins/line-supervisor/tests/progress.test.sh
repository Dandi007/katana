#!/usr/bin/env bash
# 临时夹具中执行真实读数段与 Monitor 函数，不调用生产服务。
set -euo pipefail
python3 "$(dirname "$0")/progress_test.py"
