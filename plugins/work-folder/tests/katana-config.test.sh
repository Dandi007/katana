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
# (g) env KATANA_KB_ROOT wins over config kb_root key when both present.
# ---------------------------------------------------------------------------
cfg_g="$TMP/dot_katana_g"
printf 'kb_root=/tmp/kb_from_config\n' > "$cfg_g"
out_g="$(
    set -uo pipefail
    export HOME="$TMP/home_g"; mkdir -p "$HOME"
    cd "$TMP" || exit 1
    unset CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_CONFIG_FILE="$cfg_g"
    export KATANA_KB_ROOT="/tmp/kb_from_env"
    # shellcheck disable=SC1090
    . "$CONFIG_SH"
    printf '%s' "$(katana_kb_root)"
)"
check "(g) env KATANA_KB_ROOT wins over config kb_root" "/tmp/kb_from_env" "$out_g"

# ---------------------------------------------------------------------------
# (h) ~user (no slash) is NOT bash ~user-expanded; treated as relative and
#     joined to kb_root (safe, no uncontrolled username expansion). Pins 3(c).
# ---------------------------------------------------------------------------
out_h="$(
    set -uo pipefail
    export HOME="$TMP/home_h"; mkdir -p "$HOME"
    cd "$TMP" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="/tmp/kb"
    # shellcheck disable=SC1090
    . "$CONFIG_SH"
    printf '%s' "$(katana_resolve_path '~alice')"
)"
check "(h) ~user not expanded, joins kb_root" "/tmp/kb/~alice" "$out_h"

# ---------------------------------------------------------------------------
# (i) Intended narrowing: no env, no CLAUDE_PROJECT_DIR, a stray .katana in
#     cwd is NOT auto-discovered (old $(pwd)/.katana fallback removed on
#     purpose — cwd is no longer assumed to be the KB). Returns the default.
# ---------------------------------------------------------------------------
stray="$TMP/stray"; mkdir -p "$stray"
printf 'work_folder_path=STRAY_SHOULD_NOT_BE_READ\n' > "$stray/.katana"
out_i="$(
    set -uo pipefail
    export HOME="$TMP/home_i"; mkdir -p "$HOME"   # no ~/.katana here
    cd "$stray" || exit 1
    unset KATANA_CONFIG_FILE KATANA_KB_ROOT CLAUDE_PROJECT_DIR 2>/dev/null || true
    # shellcheck disable=SC1090
    . "$CONFIG_SH"
    printf '%s' "$(katana_config_get work_folder_path 'THE_DEFAULT')"
)"
check "(i) stray cwd .katana NOT auto-discovered" "THE_DEFAULT" "$out_i"

# ---------------------------------------------------------------------------
# (j)-(m) CLI dispatch (run directly, not sourced): get/resolve/kb-root.
#   Skill bodies invoke `bash katana-config.sh resolve <key>` at load-time to
#   get a kb-root-absolute path (Claude Code dynamic injection). Sourcing must
#   NOT trigger the dispatch.
# ---------------------------------------------------------------------------
cli_kb="$TMP/cli_kb"; mkdir -p "$cli_kb/.katana-writing"
printf 'writing_dir=.katana-writing\n' > "$cli_kb/.katana"
j_get="$(KATANA_CONFIG_FILE="$cli_kb/.katana" bash "$CONFIG_SH" get writing_dir '' KATANA_WRITING_DIR)"
check "(j) CLI get returns raw value" ".katana-writing" "$j_get"
k_res="$(KATANA_CONFIG_FILE="$cli_kb/.katana" KATANA_KB_ROOT="$cli_kb" bash "$CONFIG_SH" resolve writing_dir '' KATANA_WRITING_DIR)"
check "(k) CLI resolve returns kb-root-absolute" "$cli_kb/.katana-writing" "$k_res"
l_root="$(KATANA_KB_ROOT="$cli_kb" bash "$CONFIG_SH" kb-root)"
check "(l) CLI kb-root echoes KB root" "$cli_kb" "$l_root"
m_rc=0
bash "$CONFIG_SH" bogus_subcmd >/dev/null 2>&1 || m_rc=$?
check "(m) CLI unknown subcommand exits 2" "2" "$m_rc"
# Sourcing must not auto-run the dispatch (would error/echo usage on no args).
n_src="$(set -uo pipefail; . "$CONFIG_SH" 2>&1; printf 'SOURCED_CLEAN')"
check "(n) sourcing does not trigger CLI dispatch" "SOURCED_CLEAN" "$n_src"

# ---------------------------------------------------------------------------
echo "-------------------------------------------"
echo "katana-config: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
