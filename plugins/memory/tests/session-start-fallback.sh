#!/usr/bin/env bash
# Tests tier-2 download failure → tier-3 build → run, and tier-1 cache short-circuit.
set -euo pipefail
PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fail() { echo "FAIL: $1" >&2; exit 1; }

# Helper: create a minimal valid memory card so the binary emits JSON output
make_mem_dir() {
    local d
    d="$(mktemp -d)"
    cat > "${d}/test.md" <<'EOF'
---
name: test-fact
description: a test memory card
status: active
---
Body text.
EOF
    echo "$d"
}

# Case 1: bad release base → falls through to cargo build, still emits valid JSON
rm -rf "${PLUGIN_DIR}/bin"
out=$(KATANA_MEMORY_RELEASE_BASE="http://127.0.0.1:1/none" \
      CLAUDE_MEMORY_SYSTEM_DIR="$(make_mem_dir)" \
      CLAUDE_MEMORY_PROJECT_DIR="$(make_mem_dir)" \
      "${PLUGIN_DIR}/hooks/session-start")
echo "$out" | python3 -m json.tool > /dev/null || fail "tier-3 output not valid JSON"

# Case 2: cached versioned binary short-circuits — stub proves tier-1 path is taken
PLUGIN_VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    "${PLUGIN_DIR}/.claude-plugin/plugin.json" 2>/dev/null | head -1 || true)"
mkdir -p "${PLUGIN_DIR}/bin"
cat > "${PLUGIN_DIR}/bin/claude-memory-scan" <<STUB
#!/usr/bin/env bash
if [ "\${1:-}" = "--version" ]; then echo "${PLUGIN_VERSION}"; else echo '{"tier1_stub":true}'; fi
STUB
chmod +x "${PLUGIN_DIR}/bin/claude-memory-scan"
out=$(KATANA_MEMORY_RELEASE_BASE="http://127.0.0.1:1/none" \
      CLAUDE_MEMORY_SYSTEM_DIR="$(make_mem_dir)" \
      CLAUDE_MEMORY_PROJECT_DIR="$(make_mem_dir)" \
      "${PLUGIN_DIR}/hooks/session-start")
rm -rf "${PLUGIN_DIR}/bin"
echo "$out" | grep -q '"tier1_stub":true' || fail "tier-1 short-circuit not proven (stub not reached)"

echo "PASS"
