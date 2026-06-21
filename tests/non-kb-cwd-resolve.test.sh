#!/usr/bin/env bash
# tests/non-kb-cwd-resolve.test.sh — integration test for KB-root resolution
# from an arbitrary (non-KB) cwd with a user-level ~/.katana config.
#
# Scenario (mirrors real deployment: .katana lives at ~/.katana, the agent runs
# from some unrelated directory, KATANA_KB_ROOT points at the KB):
#   - a temp KB holding the activation conditions for every path-bearing hook
#     (WIKI.md, memory/<card>.md, .katana-writing/, docs/feishu/)
#   - a temp HOME with ~/.katana carrying RELATIVE config values
#   - KATANA_KB_ROOT exported to the temp KB
#   - cwd set to a temp dir that has nothing to do with the KB
#
# For each of the 5 path-bearing session-start hooks we assert (positive) the
# injected additionalContext carries the "<KB>/..." ABSOLUTE path, and (negative)
# it never leaks a bare relative config value, the unrelated cwd, or a stray
# CLAUDE_PROJECT_DIR. (The guide hook carries no path and is out of scope.)
#
# bash 3.2 compatible; C-locale safe (no multibyte allowlist; uses ASCII-only
# config values so assertions hold under any locale).
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"
HOME_DIR="$TMP/home"
CWD="$TMP/elsewhere"
mkdir -p "$KB" "$HOME_DIR" "$CWD"

# --- KB activation conditions (ASCII-only relative paths) ---------------------
# Use distinctive, unlikely-to-collide segment names so the "no bare relative"
# negative assertion is not tripped by the word appearing in injected skill
# prose (e.g. the wiki SKILL.md naturally contains the word "wiki"). These are
# arbitrary directory names — what matters is that they resolve under kb-root.
WIKI_REL="kbtest-wiki-root"
WRITING_REL="kbtest-writing-dir"
FEISHU_REL="kbtest/feishu-mirror"
WF_REL="kbtest-work-records"
MEM_REL="kbtest-memory"

mkdir -p "$KB/$WIKI_REL" "$KB/$WRITING_REL" "$KB/$FEISHU_REL" "$KB/$MEM_REL"
touch "$KB/$WIKI_REL/WIKI.md"
cat > "$KB/$MEM_REL/sample.md" <<'EOF'
---
name: nonkb-card
description: card resolved from kb-root with non-KB cwd
---
EOF

# --- user-level ~/.katana with RELATIVE values --------------------------------
cat > "$HOME_DIR/.katana" <<EOF
work_folder_path=$WF_REL
wiki_root=$WIKI_REL
memory_project_dir=$MEM_REL
writing_dir=$WRITING_REL
feishu_docs_root=$FEISHU_REL
EOF

# Run a single hook in the isolated environment, echo its stdout.
run_hook() {
    local plugin="$1"
    (
        set -uo pipefail
        export HOME="$HOME_DIR"
        export KATANA_KB_ROOT="$KB"
        # Defensive: ensure no project-mode / env override bleeds through.
        unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR 2>/dev/null || true
        unset KATANA_WORK_FOLDER KATANA_WIKI_ROOT KATANA_WRITING_DIR 2>/dev/null || true
        unset KATANA_FEISHU_DOCS_ROOT CLAUDE_MEMORY_PROJECT_DIR 2>/dev/null || true
        cd "$CWD" || exit 1
        bash "$REPO/plugins/$plugin/hooks/session-start"
    )
}

# Shared negative assertions applied to every hook's output:
#   - no leak of the unrelated cwd
#   - no stray CLAUDE_PROJECT_DIR token
neg_common() {
    local label="$1" out="$2"
    case "$out" in
        *"$CWD"*) bad "$label / no cwd leak" "cwd [$CWD] leaked into output" ;;
        *) ok "$label / no cwd leak" ;;
    esac
    case "$out" in
        *CLAUDE_PROJECT_DIR*) bad "$label / no CLAUDE_PROJECT_DIR residue" "CLAUDE_PROJECT_DIR token present" ;;
        *) ok "$label / no CLAUDE_PROJECT_DIR residue" ;;
    esac
}

# Assert an absolute "<KB>/<rel>" appears, and the bare relative does NOT appear
# on its own (i.e. it only ever shows up as the tail of the absolute path).
assert_abs_no_bare() {
    local label="$1" out="$2" rel="$3"
    case "$out" in
        *"$KB/$rel"*) ok "$label / absolute KB path present" ;;
        *) bad "$label / absolute KB path present" "missing [$KB/$rel] in: $out" ;;
    esac
    # Strip every occurrence of the absolute path, then look for the bare rel.
    # If the bare relative still appears, it was injected un-resolved.
    local stripped="${out//$KB\/$rel/}"
    case "$stripped" in
        *"$rel"*) bad "$label / no bare relative value" "bare [$rel] leaked (not joined to kb-root)" ;;
        *) ok "$label / no bare relative value" ;;
    esac
}

# ----------------------------------------------------------------------------
# work-folder
out="$(run_hook work-folder)"
assert_abs_no_bare "work-folder" "$out" "$WF_REL"
neg_common "work-folder" "$out"

# wiki
out="$(run_hook wiki)"
assert_abs_no_bare "wiki" "$out" "$WIKI_REL"
neg_common "wiki" "$out"

# writing
out="$(run_hook writing)"
assert_abs_no_bare "writing" "$out" "$WRITING_REL"
neg_common "writing" "$out"

# feishu-docs
out="$(run_hook feishu-docs)"
assert_abs_no_bare "feishu-docs" "$out" "$FEISHU_REL"
neg_common "feishu-docs" "$out"

# memory — footer reports project=<KB>/memory and the card name is scanned.
out="$(run_hook memory)"
case "$out" in
    *"project=$KB/$MEM_REL"*) ok "memory / project dir resolved to kb-root" ;;
    *) bad "memory / project dir resolved to kb-root" "missing [project=$KB/$MEM_REL]: $out" ;;
esac
case "$out" in
    *"nonkb-card"*) ok "memory / card under kb-root scanned" ;;
    *) bad "memory / card under kb-root scanned" "card not scanned: $out" ;;
esac
neg_common "memory" "$out"

echo "-------------------------------------------"
echo "non-KB cwd resolve integration: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
