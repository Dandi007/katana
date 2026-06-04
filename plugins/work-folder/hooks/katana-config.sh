#!/usr/bin/env bash
# Shared config parser for katana plugins.
# Priority: env var > .katana file > default value
#
# Usage: source this file, then call katana_config_get <key> <default> [env_var]
# The .katana file is searched in CLAUDE_PROJECT_DIR or current directory.
#
# NOTE: Keep copies in sync across plugins:
#   plugins/work-folder/hooks/katana-config.sh
#   plugins/memory/hooks/katana-config.sh
#   plugins/wiki/hooks/katana-config.sh

# Parse .katana file: skip comments and empty lines, extract key=value
katana_config_get() {
    local key="$1"
    local default="${2:-}"
    local env_var="${3:-}"
    local config_file="${KATANA_CONFIG_FILE:-${CLAUDE_PROJECT_DIR:-$(pwd)}/.katana}"

    # Priority 1: environment variable (if specified)
    if [ -n "$env_var" ] && [ -n "${!env_var:-}" ]; then
        printf '%s' "${!env_var}"
        return
    fi

    # Priority 2: .katana file (exact key match via awk, trim whitespace, strip inline comments)
    if [ -f "$config_file" ]; then
        local value
        value="$(awk -F= -v k="$key" '$1 == k {v=substr($0, length($1)+2); sub(/#.*/, "", v); gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); print v; exit}' "$config_file" 2>/dev/null || true)"
        if [ -n "$value" ]; then
            printf '%s' "$value"
            return
        fi
    fi

    # Priority 3: default
    printf '%s' "$default"
}
