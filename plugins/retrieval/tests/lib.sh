#!/usr/bin/env bash
# Shared E2E helpers. 所有写操作落 $WORK_DIR；本地 KB 只读。
set -uo pipefail

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/retrieval-e2e.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
export WORK_DIR

PASS=0; FAIL=0; SKIP=0
pass(){ echo "PASS  $1"; PASS=$((PASS+1)); }
fail(){ echo "FAIL  $1 -- $2"; FAIL=$((FAIL+1)); }
skip(){ echo "SKIP  $1 -- $2"; SKIP=$((SKIP+1)); }

# 断言 stdout 含子串
assert_contains(){ # name, haystack, needle
  case "$2" in *"$3"*) pass "$1";; *) fail "$1" "missing '$3'";; esac
}

summary(){ echo "---"; echo "PASS=$PASS FAIL=$FAIL SKIP=$SKIP"; [ "$FAIL" -eq 0 ]; }
