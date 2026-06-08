#!/usr/bin/env bash
# Byte-parity regression test for the pure-awk memory scanner.
#
# expected.json is a frozen golden captured from the original Rust
# `claude-memory-scan` binary (serde_yaml + serde_json). The shell scanner
# MUST reproduce it byte-for-byte on the fixture corpus, which covers:
# untyped/typed grouping, type sorting, status filtering, inline-comment
# stripping, quoted scalars, and skipped (missing-name / no-frontmatter) cards.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_DIR="$(cd "${HERE}/.." && pwd)"
AWK="${PLUGIN_DIR}/hooks/scan-memory.awk"
FX="${HERE}/fixtures"

# Stable, machine-independent path args (golden was captured with these).
SYS_ARG="tests/fixtures/system"
PROJ_ARG="tests/fixtures/project"

# Build byte-sorted file lists (parity with Rust PathBuf sort).
sysfiles=(); projfiles=()
while IFS= read -r -d '' f; do sysfiles+=("$f"); done \
  < <(find "${FX}/system" -maxdepth 1 -name '*.md' -print0 | LC_ALL=C sort -z)
while IFS= read -r -d '' f; do projfiles+=("$f"); done \
  < <(find "${FX}/project" -maxdepth 1 -name '*.md' -print0 | LC_ALL=C sort -z)

actual="$(awk -v nsys="${#sysfiles[@]}" -v sysdir="${SYS_ARG}" -v projdir="${PROJ_ARG}" \
  -f "${AWK}" -- "${sysfiles[@]}" "${projfiles[@]}")"

if diff -u "${FX}/expected.json" <(printf '%s\n' "$actual"); then
    echo "PASS: scan-memory.awk is byte-identical to golden"
else
    echo "FAIL: scan-memory.awk output diverged from golden" >&2
    exit 1
fi
