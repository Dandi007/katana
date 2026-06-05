#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/lib.sh"
source "$HERE/../hooks/katana-config.sh"

# .katana 取自 KATANA_CONFIG_FILE 或 CLAUDE_PROJECT_DIR；测试时显式传
SOURCES="$(katana_config_get retrieval_sources "web,reddit" "")"
echo "WORK_DIR=$WORK_DIR"
echo "sources: $SOURCES"

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
