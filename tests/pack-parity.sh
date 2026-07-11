#!/usr/bin/env bash
# pack-parity.sh — regression gate for the "published package is missing runtime
# assets" failure class.
#
# The bug this guards against (2026-06-08): the npm `files` allowlist shipped
# plugins/*/hooks + skills but not runtime-critical plugin assets, so packed
# installs could silently lose context injection. Memory is now MCP-backed (no
# filesystem/AWK scanner): the artifact must ship the Claude SessionStart curl
# hook plus the current Kimi/OpenCode runtime clients under plugins/*/runtimes.
#
# This test packs the ACTUAL artifact (`npm pack`), extracts it, and exercises
# runtime-critical files FROM THE EXTRACTED PACKAGE. It needs only node + bash +
# python/curl available on the CI runner.
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
[ -f "${PKG}/plugins/memory/hooks/session-start" ] || fail "memory SessionStart hook not in package"
[ -f "${PKG}/plugins/memory/runtimes/kimi-code/user-prompt-hook" ] || fail "memory kimi-code runtime client not in package"
[ -f "${PKG}/plugins/memory/runtimes/opencode/katana-memory-index.ts" ] || fail "memory OpenCode runtime client not in package"
[ -f "${PKG}/plugins/memory/runtimes/install.sh" ] || fail "memory runtimes install.sh not in package"
[ -f "${PKG}/plugins/work-folder/rules/work-folder.md" ] || fail "work-folder rules/ not in package"

# --- memory Claude hook must inject <memory-index> from the packed copy -------
# Service is unreachable here → hook must still emit valid fallback JSON that
# carries a <memory-index> block and exit 0 (curl-with-degradation contract).
echo "==> memory SessionStart hook (from packed artifact)"
mem_out="$(KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 \
           bash "${PKG}/plugins/memory/hooks/session-start")"
case "$mem_out" in
    *'<memory-index>'*) echo "   ok: memory injected <memory-index>" ;;
    *) fail "memory hook produced no <memory-index> from packed artifact" ;;
esac

# --- memory kimi-code runtime client must inject index from packed copy -------
# Spin up a stub HTTP server serving /t/<tenant>/index.md; the packed client
# must fetch and emit <memory-index> on the first prompt of a session.
echo "==> memory kimi-code runtime client (from packed artifact)"
export XDG_RUNTIME_DIR="${WORK}/xdg"
mkdir -p "$XDG_RUNTIME_DIR"
srv_dir="$(mktemp -d)"
mkdir -p "${srv_dir}/t/uther"
printf '<memory-index>\n- [m-000000] packed-card — desc\n</memory-index>\n' > "${srv_dir}/t/uther/index.md"
port_file="${WORK}/port"
python3 - "$srv_dir" "$port_file" >/dev/null 2>&1 <<'PY' &
import http.server, socketserver, sys, functools, pathlib
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=sys.argv[1])
with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
    pathlib.Path(sys.argv[2]).write_text(str(httpd.server_address[1]))
    httpd.serve_forever()
PY
srv_pid=$!
trap 'kill "$srv_pid" 2>/dev/null || true; rm -rf "$WORK" "$srv_dir"' EXIT
for _ in $(seq 1 50); do [ -s "$port_file" ] && break; sleep 0.1; done
[ -s "$port_file" ] || fail "stub http server for kimi client did not start"
kimi_url="http://127.0.0.1:$(cat "$port_file")"
kimi_out="$(printf '{"session_id":"pack-parity"}' \
            | KATANA_MEMORY_MCP_URL="$kimi_url" \
              bash "${PKG}/plugins/memory/runtimes/kimi-code/user-prompt-hook")"
case "$kimi_out" in
    '<memory-index>'*) echo "   ok: kimi client injected <memory-index>" ;;
    *) fail "kimi runtime client produced no <memory-index> from packed artifact" ;;
esac
kill "$srv_pid" 2>/dev/null || true

# --- memory OpenCode runtime client must ship its injection transform ---------
# The OpenCode client is a TS plugin (loaded by OpenCode's runtime, not node
# here); assert the packed copy exposes the current system-prompt transform hook
# so packaging can never silently drop it.
echo "==> memory OpenCode runtime client (from packed artifact)"
grep -q 'experimental.chat.system.transform' \
    "${PKG}/plugins/memory/runtimes/opencode/katana-memory-index.ts" \
    || fail "OpenCode runtime client missing system.transform hook in packed artifact"
echo "   ok: OpenCode client ships system.transform injection"

# --- work-folder hook must inject its convention from the packed copy --------
echo "==> work-folder hook (from packed artifact)"
wf_out="$(CLAUDE_PROJECT_DIR="$WORK" bash "${PKG}/plugins/work-folder/hooks/session-start" <<<'{"source":"startup"}')"
[ -n "$wf_out" ] || fail "work-folder hook produced empty output from packed artifact"
echo "   ok: work-folder injected ${#wf_out} bytes"

echo "PASS: packed artifact ships memory (Claude/Kimi/OpenCode) + work-folder runtime assets"
