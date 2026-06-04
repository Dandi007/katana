#!/usr/bin/env bash
# Shared config parser for katana plugins.
# Priority: env var > .katana file > default value
#
# Usage: source this file, then call katana_config_get <key> <default> [env_var]
# The .katana file is searched in CLAUDE_PROJECT_DIR or current directory.

set -euo pipefail

KATANA_CONFIG_FILE="${KATANA_CONFIG_FILE:-${CLAUDE_PROJECT_DIR:-$(pwd)}/.katana}"

# Parse .katana file: skip comments and empty lines, extract key=value
katana_config_get() {
    local key="$1"
    local default="${2:-}"
    local env_var="${3:-}"

    # Priority 1: environment variable (if specified)
    if [ -n "$env_var" ] && [ -n "${!env_var:-}" ]; then
        printf '%s' "${!env_var}"
        return
    fi

    # Priority 2: .katana file
    if [ -f "$KATANA_CONFIG_FILE" ]; then
        local value
        value="$(grep "^${key}=" "$KATANA_CONFIG_FILE" 2>/dev/null | cut -d= -f2- | head -1 || true)"
        if [ -n "$value" ]; then
            printf '%s' "$value"
            return
        fi
    fi

    # Priority 3: default
    printf '%s' "$default"
}
