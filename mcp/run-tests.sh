#!/usr/bin/env bash
# mcp server 测试 gate：shared(kernel) + wiki + work-folder + memory 四包的 L0 单元 +
# 集成回归 + 治理 Full VFS/policy parity + 单一治理写管线 + 事务持久化 failpoint +
# 异步 push/projection 可观测性测试（进程退出码即结果，design §9.1）。
#
# 覆盖锚点（相对 2026-07-11 base snapshot 的变化，均有解释）：
#   - 移除 legacy 直写路径的 test_gitops.py / pages.git_commit 单测（那两条平行写链
#     已删除，改由 shared TransactionEngine 统一发布，design §4.4/INV-5）；
#   - 新增 test_governed_pipeline.py（三域各一份，证明 domain tools 与 fs_* 进入
#     同一 policy→MutationBatch→transaction/manifest 管线，无 raw bypass）；
#   - 新增 test_kernel_durability.py / test_projection.py（writer-private staging、
#     dirty-tree fail-stop、catalog 原子提交、push/projection checkpoint/freshness）。
# 用 --import-mode=importlib 避开四包同名 `tests` 包的 collection 冲突。
# 用法：PYTHON=/path/to/venv/bin/python bash mcp/run-tests.sh [pytest 额外参数]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
exec "$PY" -m pytest \
  "$HERE/shared/tests" "$HERE/wiki/tests" "$HERE/work-folder/tests" "$HERE/memory/tests" \
  --import-mode=importlib -p no:cacheprovider "$@"
