#!/usr/bin/env bash
# Unit tests for katana-config.sh: kb-root anchor, path resolver, and
# user-level (~/.katana) config discovery.
#
# Pure-bash assertions: source the canonical config file, call the functions
# with controlled env (KATANA_KB_ROOT / KATANA_CONFIG_FILE / CLAUDE_PROJECT_DIR
# / HOME), and compare echoed output. Temp KB/HOME/.katana via mktemp -d so the
# real $HOME/.katana is never touched. bash 3.2 compatible.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG_SH="$(cd "${HERE}/.." && pwd)/hooks/katana-config.sh"

pass=0
fail=0

check() {
    # check <label> <expected> <actual>
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        pass=$((pass + 1))
        printf 'PASS: %s\n' "$label"
    else
        fail=$((fail + 1))
        printf 'FAIL: %s\n  expected: [%s]\n  actual:   [%s]\n' "$label" "$expected" "$actual" >&2
    fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
# (a) KATANA_KB_ROOT set, cwd != KB: resolve relative path joins kb_root.
# (b) absolute path passed through unchanged.
# (c) ~/m expands HOME.
# (f) empty input -> empty output.
# Run these in a subshell with a controlled HOME so ~ expansion is deterministic.
# ---------------------------------------------------------------------------
out="$(
    set -uo pipefail
    export HOME="$TMP/home_a"
    mkdir -p "$HOME"
    cd "$TMP" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="/tmp/kb"
    # shellcheck disable=SC1090
    . "$CONFIG_SH"
    printf 'A:%s\n' "$(katana_resolve_path '智元工作/x')"
    printf 'B:%s\n' "$(katana_resolve_path '/abs/path')"
    printf 'C:%s\n' "$(katana_resolve_path '~/m')"
    printf 'F:%s\n' "$(katana_resolve_path '')"
)"
a_val="$(printf '%s\n' "$out" | sed -n 's/^A://p')"
b_val="$(printf '%s\n' "$out" | sed -n 's/^B://p')"
c_val="$(printf '%s\n' "$out" | sed -n 's/^C://p')"
f_line="$(printf '%s\n' "$out" | grep -c '^F:$' || true)"

check "(a) relative joins kb_root" "/tmp/kb/智元工作/x" "$a_val"
check "(b) absolute passthrough" "/abs/path" "$b_val"
check "(c) ~/ expands HOME" "$TMP/home_a/m" "$c_val"
check "(f) empty input -> empty" "1" "$f_line"

# ---------------------------------------------------------------------------
# (d) No KATANA_KB_ROOT, but kb_root=/tmp/kb2 written in a temp .katana
#     (pointed to via KATANA_CONFIG_FILE) -> katana_kb_root takes it.
# ---------------------------------------------------------------------------
cfg_d="$TMP/dot_katana_d"
printf 'kb_root=/tmp/kb2\n' > "$cfg_d"
out_d="$(
    set -uo pipefail
    export HOME="$TMP/home_d"
    mkdir -p "$HOME"
    cd "$TMP" || exit 1
    unset KATANA_KB_ROOT CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_CONFIG_FILE="$cfg_d"
    # shellcheck disable=SC1090
    . "$CONFIG_SH"
    printf '%s' "$(katana_kb_root)"
)"
check "(d) kb_root from .katana config key" "/tmp/kb2" "$out_d"

# ---------------------------------------------------------------------------
# (e) KATANA_CONFIG_FILE unset, CLAUDE_PROJECT_DIR has no .katana, but
#     $HOME/.katana exists -> katana_config_get reads the user-level value.
# ---------------------------------------------------------------------------
home_e="$TMP/home_e"
mkdir -p "$home_e"
printf 'work_folder_path=user/level/value\n' > "$home_e/.katana"
proj_e="$TMP/proj_e"   # deliberately has NO .katana
mkdir -p "$proj_e"
out_e="$(
    set -uo pipefail
    export HOME="$home_e"
    cd "$TMP" || exit 1
    unset KATANA_CONFIG_FILE KATANA_KB_ROOT 2>/dev/null || true
    export CLAUDE_PROJECT_DIR="$proj_e"
    # shellcheck disable=SC1090
    . "$CONFIG_SH"
    printf '%s' "$(katana_config_get work_folder_path '')"
)"
check "(e) user-level ~/.katana discovery" "user/level/value" "$out_e"

# ---------------------------------------------------------------------------
echo "-------------------------------------------"
echo "katana-config: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
