#!/usr/bin/env bash
# pack-parity.sh — regression gate for the "published package is missing runtime
# assets" failure class.
#
# The bug this guards against (2026-06-08): the npm `files` allowlist shipped
# plugins/*/hooks + skills but NOT the memory scanner nor plugins/*/rules, so on
# OpenCode the memory and work-folder SessionStart hooks emitted nothing — and no
# test caught it because every existing test ran against the working tree (where
# all assets are present) or mocked spawn entirely.
#
# This test packs the ACTUAL artifact (`npm pack`), extracts it, and runs the
# runtime-critical hooks FROM THE EXTRACTED PACKAGE, asserting they inject
# non-empty context. It needs only node + bash + awk.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
fail() { echo "FAIL: $1" >&2; exit 1; }

echo "==> npm pack"
TARBALL="$(cd "$ROOT" && npm pack --silent)"
[ -f "${ROOT}/${TARBALL}" ] || fail "npm pack produced no tarball"
tar -xzf "${ROOT}/${TARBALL}" -C "$WORK"
rm -f "${ROOT}/${TARBALL}"
PKG="${WORK}/package"
[ -d "$PKG" ] || fail "extracted package/ not found"

# --- assert runtime assets actually shipped ---------------------------------
[ -f "${PKG}/plugins/memory/hooks/session-start" ] || fail "memory session-start hook not in package"
[ -f "${PKG}/plugins/work-folder/rules/work-folder.md" ] || fail "work-folder rules/ not in package"

# --- memory hook must emit valid fallback JSON from the packed copy ----------
# (memory 已 MCP 化：hook 查询 katana-memory-mcp，服务不可达时必须降级出合法 JSON)
echo "==> memory hook (from packed artifact, service unreachable)"
mem_out="$(KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 \
           bash "${PKG}/plugins/memory/hooks/session-start" <<<'{"source":"startup"}')"
echo "$mem_out" | python3 -c "import json,sys; d=json.load(sys.stdin); assert 'unavailable' in d['hookSpecificOutput']['additionalContext']" \
    || fail "memory hook fallback JSON invalid from packed artifact"
echo "   ok: memory hook emitted valid fallback JSON"

# --- work-folder hook must inject its convention from the packed copy --------
echo "==> work-folder hook (from packed artifact)"
wf_out="$(CLAUDE_PROJECT_DIR="$WORK" bash "${PKG}/plugins/work-folder/hooks/session-start" <<<'{"source":"startup"}')"
[ -n "$wf_out" ] || fail "work-folder hook produced empty output from packed artifact"
echo "   ok: work-folder injected ${#wf_out} bytes"

echo "PASS: packed artifact injects memory + work-folder context"
