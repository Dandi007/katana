#!/usr/bin/env bash
# Shared config parser + KB-root resolver for katana plugins.
# Priority: env var > .katana file > default value
#
# Usage: source this file, then call:
#   katana_config_get <key> <default> [env_var]  -> echo resolved value
#   katana_kb_root                                -> echo KB root (absolute)
#   katana_resolve_path <relative_or_absolute>   -> echo absolute path
#
# Config discovery: KATANA_CONFIG_FILE (if set) > $CLAUDE_PROJECT_DIR/.katana
# (if exists) > ~/.katana (if exists). First existing file wins. This lets the
# .katana config live at the user level, decoupled from cwd.
#
# bash 3.2 compatible (no `declare -A`, no `${var^^}`); C-locale safe (path
# handling uses byte-level `case` globs, no multibyte allowlist regex).
#
# NOTE: Keep copies in sync across plugins (byte-identical):
#   plugins/work-folder/hooks/katana-config.sh
#   plugins/memory/hooks/katana-config.sh
#   plugins/wiki/hooks/katana-config.sh
#   plugins/feishu-docs/hooks/katana-config.sh
#   plugins/retrieval/hooks/katana-config.sh
#   plugins/writing/hooks/katana-config.sh

# Holds the .katana file path actually adopted by the most recent
# katana_config_get / katana__resolve_config_file call, for kb_root fallback.
KATANA_RESOLVED_CONFIG_FILE=""

# Resolve which .katana file to use and store it in KATANA_RESOLVED_CONFIG_FILE.
# Order: KATANA_CONFIG_FILE (explicit, used as-is) > $CLAUDE_PROJECT_DIR/.katana
# (if exists) > ~/.katana (if exists). Empty if none found.
katana__resolve_config_file() {
    if [ -n "${KATANA_CONFIG_FILE:-}" ]; then
        KATANA_RESOLVED_CONFIG_FILE="$KATANA_CONFIG_FILE"
        return
    fi
    if [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -f "${CLAUDE_PROJECT_DIR}/.katana" ]; then
        KATANA_RESOLVED_CONFIG_FILE="${CLAUDE_PROJECT_DIR}/.katana"
        return
    fi
    if [ -n "${HOME:-}" ] && [ -f "${HOME}/.katana" ]; then
        KATANA_RESOLVED_CONFIG_FILE="${HOME}/.katana"
        return
    fi
    KATANA_RESOLVED_CONFIG_FILE=""
}

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

    # Priority 2: .katana file (discovery: explicit > project > user-level)
    katana__resolve_config_file
    local config_file="$KATANA_RESOLVED_CONFIG_FILE"
    if [ -n "$config_file" ] && [ -f "$config_file" ]; then
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

# Echo the KB root (absolute). Resolution order:
#   1. $KATANA_KB_ROOT env (non-empty)
#   2. config key `kb_root` (read directly from the adopted .katana to avoid
#      recursing through path resolution)
#   3. directory containing the adopted .katana file (if it exists)
#   4. $CLAUDE_PROJECT_DIR (non-empty)
#   5. $(pwd)
katana_kb_root() {
    if [ -n "${KATANA_KB_ROOT:-}" ]; then
        printf '%s' "$KATANA_KB_ROOT"
        return
    fi

    katana__resolve_config_file
    local config_file="$KATANA_RESOLVED_CONFIG_FILE"
    if [ -n "$config_file" ] && [ -f "$config_file" ]; then
        local kb
        kb="$(awk -F= -v k="kb_root" '$1 == k {v=substr($0, length($1)+2); sub(/#.*/, "", v); gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); print v; exit}' "$config_file" 2>/dev/null || true)"
        if [ -n "$kb" ]; then
            printf '%s' "$kb"
            return
        fi
        # Fall back to the directory holding the adopted .katana file.
        local dir
        dir="$(dirname "$config_file")"
        if [ -n "$dir" ]; then
            printf '%s' "$dir"
            return
        fi
    fi

    if [ -n "${CLAUDE_PROJECT_DIR:-}" ]; then
        printf '%s' "$CLAUDE_PROJECT_DIR"
        return
    fi

    printf '%s' "$(pwd)"
}

# Echo an absolute path for the given relative-or-absolute input.
#   empty       -> echo empty (preserves the "unset = silent" contract)
#   ~ or ~/...  -> expand $HOME
#   /...        -> passthrough unchanged
#   otherwise   -> "$(katana_kb_root)/$input"
katana_resolve_path() {
    local p="${1:-}"
    if [ -z "$p" ]; then
        return
    fi
    case "$p" in
        '~')
            printf '%s' "$HOME"
            ;;
        '~/'*)
            printf '%s' "${HOME}/${p#'~/'}"
            ;;
        /*)
            printf '%s' "$p"
            ;;
        *)
            printf '%s' "$(katana_kb_root)/$p"
            ;;
    esac
}
