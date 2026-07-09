#!/usr/bin/env bash
# Regression: wiki_root validation must be locale-independent.
#
# History: an allowlist ERE with a CJK multibyte range (`一-鿿`) made glibc
# regcomp fail under UTF-8 locales ("Invalid collation character"), so EVERY
# legitimate wiki_root (even ".") was rejected and the hook always exited 1 —
# the using-wiki convention never got injected. Fixed by switching to a
# byte-based `case` denylist.
#
# This test runs the hook under every UTF-8 locale available on the host and
# asserts: (a) a legitimate wiki_root injects successfully, (b) ".." traversal
# is still rejected, (c) a NEWLINE in wiki_root is rejected. The newline case
# matters because newline is the record separator for grep/ERE — a grep-based
# denylist would miss it, but `case` globbing catches it byte-wise.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
KB="$TMP/kb"; mkdir -p "$KB"; : > "$KB/WIKI.md"

run_under() {  # $1 = locale
    local loc="$1" out rc

    # (a) legitimate wiki_root injects the using-wiki convention
    out=$(env LC_ALL="$loc" KATANA_KB_ROOT="$KB" KATANA_WIKI_ROOT="." bash "$HOOK" 2>/dev/null); rc=$?
    case "$out" in
        *hookSpecificOutput*) ok "[$loc] legitimate wiki_root injects" ;;
        *) bad "[$loc] legitimate wiki_root injects" "rc=$rc out=${out:0:80}" ;;
    esac

    # (b) path traversal rejected
    env LC_ALL="$loc" KATANA_KB_ROOT="$KB" KATANA_WIKI_ROOT="../etc" bash "$HOOK" >/dev/null 2>&1; rc=$?
    if [ "$rc" -ne 0 ]; then ok "[$loc] path traversal rejected"
    else bad "[$loc] path traversal rejected" "rc=$rc (expected non-zero)"; fi

    # (c) newline in wiki_root rejected (grep/ERE would miss it; case catches it)
    env LC_ALL="$loc" KATANA_KB_ROOT="$KB" KATANA_WIKI_ROOT=$'/tmp\nx' bash "$HOOK" >/dev/null 2>&1; rc=$?
    if [ "$rc" -ne 0 ]; then ok "[$loc] newline in wiki_root rejected"
    else bad "[$loc] newline in wiki_root rejected" "rc=$rc (expected non-zero)"; fi
}

# Run under every UTF-8 locale available on the host, plus plain C. The UTF-8
# locales are the ones that surface the original regcomp bug; C is included to
# confirm the case-based denylist stays correct in a byte-oriented locale too.
locales_tested=0
for loc in C en_US.UTF-8 en_US.utf8 C.UTF-8 C.utf8; do
    if locale -a 2>/dev/null | grep -qxF "$loc"; then
        run_under "$loc"; locales_tested=$((locales_tested + 1))
    fi
done
[ "$locales_tested" -gt 0 ] || bad "locale coverage" "no test locales available on host"

echo "-------------------------------------------"
echo "wiki session-start locale: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
