#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/lib.sh"
source "$HERE/../hooks/katana-config.sh"

# .katana 取自 KATANA_CONFIG_FILE 或 CLAUDE_PROJECT_DIR；测试时显式传。
# 默认空：未声明 retrieval_sources（离线 CI / 无 .katana）则不跑任何 case、summary 退 0，
# 与 session-start hook 的"未配置即静默"一致；需显式 .katana / env 才跑网络依赖 case。
SOURCES="$(katana_config_get retrieval_sources "" "")"
echo "WORK_DIR=$WORK_DIR"
echo "sources: ${SOURCES:-<none>}"

if [ -z "$SOURCES" ]; then summary; exit $?; fi

IFS=',' read -ra ARR <<< "$SOURCES"
for s in "${ARR[@]}"; do
  case_file="$HERE/cases/${s}.case.sh"
  if [ -f "$case_file" ]; then
    # shellcheck disable=SC1090
    source "$case_file"
  else
    skip "$s" "no case file"
  fi
done
summary
