#!/usr/bin/env bash
# katana parity e2e runner — run the same scenario on real Claude Code and real
# OpenCode inside fully isolated sandboxes, collect injection/skills, diff.
#
# Usage: ./run.sh <scenario.json> [cc|oc|both]   (default: both)
#
# Isolation (golden order: tests MUST NOT touch real usage):
#   - HOME      -> $SANDBOX/<side>/home
#   - TMPDIR    -> $SANDBOX/<side>/tmp
#   - OC: XDG_CONFIG_HOME/XDG_DATA_HOME/OPENCODE_DB sandboxed,
#         OPENCODE_HOST/OPENCODE_SERVER_PASSWORD scrubbed
#   - CC: CLAUDE_CONFIG_DIR sandboxed
# LLM traffic: both sides go through the New API gateway (127.0.0.1:15722;
#   cc-switch :15721 retired 2026-08-21). Bearer token from NEW_API_GATEWAY_TOKEN
#   or ~/.config/agent-shell/secrets.env — never printed, never written to disk.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E="$ROOT/parity/e2e"
SCENARIO="${1:?usage: run.sh <scenario.json> [cc|oc|both]}"
SIDE="${2:-both}"

GATEWAY_URL="${KATANA_PARITY_GATEWAY_URL:-http://127.0.0.1:15722}"
# 网关裸模型名（不带 provider 前缀）；OC 侧加 gateway/ provider 前缀，两侧同一 model
CC_MODEL="${KATANA_PARITY_CC_MODEL:-glm-5.3}"
OC_MODEL="${KATANA_PARITY_OC_MODEL:-gateway/${CC_MODEL}}"

# Gateway token: env first, else secrets.env. Never echoed.
if [ -z "${NEW_API_GATEWAY_TOKEN:-}" ] && [ -f "$HOME/.config/agent-shell/secrets.env" ]; then
  NEW_API_GATEWAY_TOKEN="$(sed -n 's/^NEW_API_GATEWAY_TOKEN=//p' "$HOME/.config/agent-shell/secrets.env" | head -1)"
fi
if [ -z "${NEW_API_GATEWAY_TOKEN:-}" ]; then
  echo "[e2e] BLOCKED: NEW_API_GATEWAY_TOKEN missing (env or ~/.config/agent-shell/secrets.env)"
  exit 2
fi
export NEW_API_GATEWAY_TOKEN

# Verify the gateway is online (authenticated model list must answer 200)
if ! curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NEW_API_GATEWAY_TOKEN" \
     "$GATEWAY_URL/v1/models" 2>/dev/null | grep -qx "200"; then
  echo "[e2e] BLOCKED: New API gateway not online at $GATEWAY_URL"
  exit 2
fi

PROMPT="$(node -e "console.log(JSON.parse(require('fs').readFileSync(process.argv[1],'utf8')).prompt)" "$ROOT/$SCENARIO")"

SANDBOX="${KATANA_PARITY_SANDBOX:-$(mktemp -d /tmp/katana-parity-e2e-XXXXXX)}"
echo "[e2e] sandbox: $SANDBOX"
echo "[e2e] scenario: $SCENARIO"
echo "[e2e] cc_model: $CC_MODEL"
echo "[e2e] oc_model: $OC_MODEL"

make_side() { # $1 = cc|oc — identical fixture both sides (byte-identical inputs)
  local side="$1" home tmp proj
  home="$SANDBOX/$side/home"; tmp="$SANDBOX/$side/tmp"; proj="$SANDBOX/$side/proj"
  mkdir -p "$home/.claude" "$tmp" "$proj" "$SANDBOX/$side/bin"
  # Activate all 4 deterministic session-start segments: guide+work-folder are
  # unconditional; retrieval needs retrieval_sources;
  # memory needs at least one card (else the hook emits nothing).
  printf 'retrieval_sources=web:web\n' > "$proj/.katana"
  # Seed one project memory card. Both sides scan $CLAUDE_PROJECT_DIR/memory
  # (= $proj/memory); system memory ($HOME/.claude/memory) is empty in-sandbox,
  # so the injected <memory-index> is byte-identical across CC and OC.
  mkdir -p "$proj/memory"
  printf -- '---\nname: parity-probe-fact\ndescription: e2e parity probe memory card\nmetadata:\n  type: project\n---\n\nProbe card so the memory session-start hook injects a <memory-index>.\n' \
    > "$proj/memory/parity-probe-fact.md"
}

run_cc() {
  make_side cc
  local home="$SANDBOX/cc/home" tmp="$SANDBOX/cc/tmp" proj="$SANDBOX/cc/proj"
  # Generate CC settings with katana hooks
  node "$E2E/lib/gen-cc-settings.cjs" "$ROOT" > "$home/.claude/settings.json"
  printf '{"hasCompletedOnboarding": true}\n' > "$home/.claude.json"
  echo "[e2e] cc: running claude -p ..."
  (
    cd "$proj"
    env -u CLAUDE_CONFIG_DIR \
      HOME="$home" TMPDIR="$tmp" PATH="$SANDBOX/cc/bin:$PATH" \
      ANTHROPIC_BASE_URL="$GATEWAY_URL" ANTHROPIC_AUTH_TOKEN="$NEW_API_GATEWAY_TOKEN" ANTHROPIC_API_KEY="" \
      CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1 \
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
  node "$E2E/lib/gen-oc-config.cjs" "$OC_MODEL" "$GATEWAY_URL" > "$SANDBOX/oc/xdg-config/opencode/opencode.json"
  ln -sf "$ROOT/parity/adapter/opencode/index.ts" "$proj/.opencode/plugin/katana-parity.ts"
  echo "[e2e] oc: running opencode run ..."
  (
    cd "$proj"
    env -u OPENCODE_HOST -u OPENCODE_SERVER_PASSWORD -u OPENCODE_SKIP_START -u OPENCODE_PORT \
      HOME="$home" TMPDIR="$tmp" PATH="$SANDBOX/oc/bin:$PATH" \
      XDG_CONFIG_HOME="$SANDBOX/oc/xdg-config" XDG_DATA_HOME="$SANDBOX/oc/xdg-data" \
      OPENCODE_DB="$SANDBOX/oc/xdg-data/opencode.db" \
      KATANA_PARITY_ROOT="$ROOT" NEW_API_GATEWAY_TOKEN="$NEW_API_GATEWAY_TOKEN" \
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
  echo "[e2e] Running verdict..."
  OPENCODE_DB="$SANDBOX/oc/xdg-data/opencode.db" node "$E2E/lib/check.cjs" "$ROOT/$SCENARIO" "$SANDBOX"
fi
