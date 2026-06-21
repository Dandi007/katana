#!/usr/bin/env bash
# Resolve test for the memory SessionStart hook.
#
# Default project memory dir must be "<kb-root>/memory" (not "<cwd>/memory").
# We create <kb>/memory with one minimal valid card, run from a non-KB cwd
# with no CLAUDE_MEMORY_PROJECT_DIR, and assert the emitted memory-index footer
# reports project=<kb>/memory and the card is scanned.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"; OTHER="$TMP/other"
mkdir -p "$KB/memory" "$OTHER"
# Minimal valid card (frontmatter name+description -> scanned).
cat > "$KB/memory/a.md" <<'EOF'
---
name: resolve-card
description: kb-root resolved memory card
---
EOF

out="$(
    set -uo pipefail
    # Use a temp HOME with no real ~/.claude/memory to avoid system cards.
    export HOME="$TMP/home"; mkdir -p "$HOME"
    cd "$OTHER" || exit 1
    unset KATANA_CONFIG_FILE CLAUDE_PROJECT_DIR CLAUDE_MEMORY_PROJECT_DIR 2>/dev/null || true
    export KATANA_KB_ROOT="$KB"
    bash "$HOOK"
)"

case "$out" in
    *"project=$KB/memory"*) ok "project memory dir defaults to kb-root/memory" ;;
    *) bad "project memory dir defaults to kb-root/memory" "missing [project=$KB/memory]: $out" ;;
esac

case "$out" in
    *"resolve-card"*) ok "card under kb-root/memory is scanned" ;;
    *) bad "card under kb-root/memory is scanned" "card not scanned: $out" ;;
esac

case "$out" in
    *"$OTHER/memory"*) bad "no cwd/memory leak" "cwd memory [$OTHER/memory] leaked" ;;
    *) ok "no cwd/memory leak" ;;
esac

echo "-------------------------------------------"
echo "memory session-start resolve: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
