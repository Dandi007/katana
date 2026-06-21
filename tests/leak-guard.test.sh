#!/usr/bin/env bash
# tests/leak-guard.test.sh
# 验证：即便宿主导出 KATANA_KB_ROOT=真实KB路径，runner 构造的 base_env 也不让真实值
# 泄进 claude 子进程的有效环境。
#
# 核心逻辑：runner 的 build_base_env 将 KATANA_KB_ROOT="" 覆盖写入 env dict；
# claude_cli.py 用 {**os.environ, **env} 合并，env 里的空字符串会覆盖 os.environ
# 的真实值。断言验证有效合并环境里 KATANA_KB_ROOT 为空（而非断言键不存在）。
#
# bash 3.2 compatible; C-locale safe.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

out=$(KATANA_KB_ROOT="/tmp/REAL_KB_SHOULD_NOT_LEAK" uv run --with pyyaml python - "$REPO" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1] + "/tests")
from harness import isolate
base = isolate.build_base_env(no_ccs_check=True)
# 模拟 trigger.py 的合并方式
effective = {**os.environ, **base}
val = effective.get("KATANA_KB_ROOT", "")
if val == "":
    print("EMPTY")
else:
    print("LEAK:" + val)
# HOME 隔离在 case_env() 层（per-attempt），base 本身不要求含 HOME
print("HOME_KEY", "HOME" in base or "(home注入在 case_env 层)")
PY
)

echo "$out" | grep -q "^EMPTY" || { echo "FAIL: KATANA_KB_ROOT leaked into effective env: $out"; exit 1; }
echo "PASS leak-guard"
