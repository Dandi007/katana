#!/usr/bin/env bash
# Local fallback must return unmigrated content and suppress migrated domains.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$(cd "${HERE}/.." && pwd)/skills/search-note/scripts/query_lancedb.py"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/root/docs" "$TMP/root/DeepThought/topic" "$TMP/root/智元工作/工作记录/task"
printf 'scope-sentinel allowed\n' > "$TMP/root/docs/allowed.md"
printf 'scope-sentinel migrated wiki\n' > "$TMP/root/DeepThought/topic/report.md"
printf 'scope-sentinel migrated work folder\n' > "$TMP/root/智元工作/工作记录/task/findings.md"

output="$(python3 "$SCRIPT" scope-sentinel \
    --mode keyword --source markdown --root "$TMP/root" --cache-dir "$TMP/cache" \
    --scope docs --scope 智元工作 \
    --exclude-scope 智元工作/工作记录)"

OUTPUT="$output" python3 - <<'PY'
import json
import os

paths = [row["path"] for row in json.loads(os.environ["OUTPUT"])["results"]]
if paths != ["docs/allowed.md"]:
    raise SystemExit(f"unexpected scoped paths: {paths}")
print("PASS: migrated scopes excluded from local fallback")
PY

if python3 "$SCRIPT" scope-sentinel --mode keyword --source markdown \
    --root "$TMP/root" --cache-dir "$TMP/cache" --scope DeepThought >/dev/null 2>&1; then
    echo "FAIL: migrated scope accepted by local fallback" >&2
    exit 1
fi
echo "PASS: migrated scope rejected by local fallback"
