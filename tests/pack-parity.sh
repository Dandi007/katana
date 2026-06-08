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
[ -f "${PKG}/plugins/memory/hooks/scan-memory.awk" ] || fail "memory scanner not in package"
[ -f "${PKG}/plugins/work-folder/rules/work-folder.md" ] || fail "work-folder rules/ not in package"

# --- memory hook must inject <memory-index> from the packed copy -------------
echo "==> memory hook (from packed artifact)"
mem_out="$(CLAUDE_MEMORY_SYSTEM_DIR="${ROOT}/plugins/memory/tests/fixtures/system" \
           CLAUDE_MEMORY_PROJECT_DIR="${ROOT}/plugins/memory/tests/fixtures/project" \
           bash "${PKG}/plugins/memory/hooks/session-start" <<<'{"source":"startup"}')"
case "$mem_out" in
    *'<memory-index>'*) echo "   ok: memory injected <memory-index>" ;;
    *) fail "memory hook produced no <memory-index> from packed artifact" ;;
esac

# --- work-folder hook must inject its convention from the packed copy --------
echo "==> work-folder hook (from packed artifact)"
wf_out="$(CLAUDE_PROJECT_DIR="$WORK" bash "${PKG}/plugins/work-folder/hooks/session-start" <<<'{"source":"startup"}')"
[ -n "$wf_out" ] || fail "work-folder hook produced empty output from packed artifact"
echo "   ok: work-folder injected ${#wf_out} bytes"

echo "PASS: packed artifact injects memory + work-folder context"
