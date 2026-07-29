#!/usr/bin/env bash
# SessionStart output must be invariant under cwd and every retired path setting.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$(cd "${HERE}/.." && pwd)/hooks/session-start"

pass=0; fail=0
ok()  { pass=$((pass+1)); printf 'PASS: %s\n' "$1"; }
bad() { fail=$((fail+1)); printf 'FAIL: %s\n  %s\n' "$1" "$2" >&2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FIRST="$TMP/first-cwd"; SECOND="$TMP/second-cwd"
mkdir -p "$FIRST" "$SECOND" "$TMP/home-a" "$TMP/home-b"
printf 'work_folder_interface=skill\nwork_folder_path=%s\n' \
    "$TMP/config-a-DO-NOT-LEAK" > "$TMP/a.katana"
printf 'work_folder_interface=mcp\nwork_folder_path=%s\n' \
    "$TMP/config-b-DO-NOT-LEAK" > "$TMP/b.katana"

out_a="$(
    set -uo pipefail
    export HOME="$TMP/home-a"
    cd "$FIRST" || exit 1
    export KATANA_CONFIG_FILE="$TMP/a.katana"
    export KATANA_KB_ROOT="$TMP/kb-a-DO-NOT-LEAK"
    export KATANA_WORK_FOLDER="$TMP/env-a-DO-NOT-LEAK"
    export KATANA_WORK_FOLDER_INTERFACE="skill"
    bash "$HOOK"
)"

out_b="$(
    set -uo pipefail
    export HOME="$TMP/home-b"
    cd "$SECOND" || exit 1
    export KATANA_CONFIG_FILE="$TMP/b.katana"
    export KATANA_KB_ROOT="$TMP/kb-b-DO-NOT-LEAK"
    export KATANA_WORK_FOLDER="$TMP/env-b-DO-NOT-LEAK"
    export KATANA_WORK_FOLDER_INTERFACE="mcp"
    bash "$HOOK"
)"

if [ "$out_a" = "$out_b" ]; then
    ok "output is independent of cwd and retired path settings"
else
    bad "output is independent of cwd and retired path settings" "outputs differ"
fi

case "$out_a$out_b" in
    *DO-NOT-LEAK*|*"$FIRST"*|*"$SECOND"*)
        bad "does not expose any client locator" "output: $out_a $out_b" ;;
    *) ok "does not expose any client locator" ;;
esac

case "$out_a" in
    *folder_id*不得推导*folder-relative*filename*) ok "requires opaque folder_id plus relative filename" ;;
    *) bad "requires opaque folder_id plus relative filename" "output: $out_a" ;;
esac

case "$out_a" in
    *fs_create*fs_write*不会隐式创建*) ok "distinguishes create from write" ;;
    *) bad "distinguishes create from write" "output: $out_a" ;;
esac

echo "-------------------------------------------"
echo "work-folder session-start opaque-id: ${pass} passing, ${fail} failing"
[ "$fail" -eq 0 ] || exit 1
