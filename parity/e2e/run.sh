#!/usr/bin/env bash
# katana parity e2e runner — run the same scenario on real Claude Code and real
# OpenCode inside fully isolated sandboxes, collect injection/fpa/skills, diff.
#
# Usage: ./run.sh <scenario.json> [cc|oc|both]   (default: both)
#
# Isolation (golden order: tests MUST NOT touch real usage):
#   - HOME      -> $SANDBOX/<side>/home
#   - TMPDIR    -> $SANDBOX/<side>/tmp
#   - OC: XDG_CONFIG_HOME/XDG_DATA_HOME/OPENCODE_DB sandboxed,
#         OPENCODE_HOST/OPENCODE_SERVER_PASSWORD scrubbed
#   - CC: CLAUDE_CONFIG_DIR sandboxed
# LLM traffic: both sides go through CC Switch proxy (127.0.0.1:15721).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E="$ROOT/parity/e2e"
SCENARIO="${1:?usage: run.sh <scenario.json> [cc|oc|both]}"
SIDE="${2:-both}"

CCS_URL="${KATANA_PARITY_CCS_URL:-http://127.0.0.1:15721}"
CC_MODEL="${KATANA_PARITY_CC_MODEL:-claude-haiku-4-5}"
OC_MODEL="${KATANA_PARITY_OC_MODEL:-ccs/claude-haiku-4-5}"

# Verify ccs is online (root path 404 = alive)
if ! curl -s -o /dev/null -w "%{http_code}" "$CCS_URL/" 2>/dev/null | grep -qE "^(200|404)$"; then
  echo "[e2e] BLOCKED: ccs not online at $CCS_URL"
  exit 2
fi

PROMPT="$(node -e "console.log(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).prompt)" "$ROOT/$SCENARIO")"

SANDBOX="${KATANA_PARITY_SANDBOX:-$(mktemp -d /tmp/katana-parity-e2e-XXXXXX)}"
echo "[e2e] sandbox: $SANDBOX"
echo "[e2e] scenario: $SCENARIO"
echo "[e2e] cc_model: $CC_MODEL"
echo "[e2e] oc_model: $OC_MODEL"

# Record start time for ccs payload query
START_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

make_side() { # $1 = cc|oc
  local side="$1" home tmp
  home="$SANDBOX/$side/home"; tmp="$SANDBOX/$side/tmp"
  mkdir -p "$home/.claude" "$tmp" "$SANDBOX/$side/proj" "$SANDBOX/$side/bin"
}

run_cc() {
  make_side cc
  local home="$SANDBOX/cc/home" tmp="$SANDBOX/cc/tmp" proj="$SANDBOX/cc/proj"
  # Generate CC settings with katana hooks
  node "$E2E/lib/gen-cc-settings.js" "$ROOT" > "$home/.claude/settings.json"
  printf '{"hasCompletedOnboarding": true}\n' > "$home/.claude.json"
  echo "[e2e] cc: running claude -p ..."
  (
    cd "$proj"
    env -u CLAUDE_CONFIG_DIR \
      HOME="$home" TMPDIR="$tmp" PATH="$SANDBOX/cc/bin:$PATH" \
      ANTHROPIC_BASE_URL="$CCS_URL" ANTHROPIC_API_KEY="katana-parity" ANTHROPIC_AUTH_TOKEN="katana-parity" \
      NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost" \
      CLAUDE_PLUGIN_ROOT="$ROOT" \
      claude -p --model "$CC_MODEL" --permission-mode bypassPermissions "$PROMPT" \
      > "$SANDBOX/cc/run.out" 2> "$SANDBOX/cc/run.err"
  ) || { echo "[e2e] cc run FAILED"; tail -5 "$SANDBOX/cc/run.err" || true; }
  collect cc "$home" "$tmp"
}

run_oc() {
  make_side oc
  local home="$SANDBOX/oc/home" tmp="$SANDBOX/oc/tmp" proj="$SANDBOX/oc/proj"
  mkdir -p "$SANDBOX/oc/xdg-config/opencode" "$SANDBOX/oc/xdg-data" "$proj/.opencode/plugin"
  node "$E2E/lib/gen-oc-config.js" "$OC_MODEL" "$CCS_URL" > "$SANDBOX/oc/xdg-config/opencode/opencode.json"
  ln -sf "$ROOT/parity/adapter/opencode/index.ts" "$proj/.opencode/plugin/katana-parity.ts"
  echo "[e2e] oc: running opencode run ..."
  (
    cd "$proj"
    env -u OPENCODE_HOST -u OPENCODE_SERVER_PASSWORD -u OPENCODE_SKIP_START -u OPENCODE_PORT \
      HOME="$home" TMPDIR="$tmp" PATH="$SANDBOX/oc/bin:$PATH" \
      XDG_CONFIG_HOME="$SANDBOX/oc/xdg-config" XDG_DATA_HOME="$SANDBOX/oc/xdg-data" \
      OPENCODE_DB="$SANDBOX/oc/xdg-data/opencode.db" \
      KATANA_PARITY_ROOT="$ROOT" \
      NO_PROXY="127.0.0.1,localhost" no_proxy="127.0.0.1,localhost" \
      opencode run "$PROMPT" \
      > "$SANDBOX/oc/run.out" 2> "$SANDBOX/oc/run.err"
  ) || { echo "[e2e] oc run FAILED"; tail -5 "$SANDBOX/oc/run.err" || true; }
  collect oc "$home" "$tmp"
}

collect() { # $1 side, $2 home, $3 tmp — gather contract effect files
  local side="$1" home="$2" tmp="$3" out="$SANDBOX/$1/collected"
  mkdir -p "$out"
  cp "$SANDBOX/$side/run.out" "$out/output.txt" 2>/dev/null || true
  cp "$SANDBOX/$side/run.err" "$out/log.txt" 2>/dev/null || true
  echo "[e2e] $side collected: $(ls "$out" 2>/dev/null | tr '\n' ' ')"
}

[ "$SIDE" = cc ] || [ "$SIDE" = both ] && run_cc
[ "$SIDE" = oc ] || [ "$SIDE" = both ] && run_oc

if [ "$SIDE" = both ]; then
  # Wait for ccs to flush payloads
  sleep 2
  echo "[e2e] Running verdict..."
  node "$E2E/lib/check.js" "$ROOT/$SCENARIO" "$SANDBOX" "$CC_MODEL" "$START_TIME"
fi
