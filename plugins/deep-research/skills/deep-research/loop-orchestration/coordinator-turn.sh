#!/bin/sh
# 一个 coordinator turn。由 Loop MCP 作为 callback 拉起，stdin 是 loop-callback/1 envelope。
#
# 契约：本脚本只做机械动作——拉起 coordinator subagent 跑一个 turn，然后看哨兵。
# 「是否收敛」的判断全在 coordinator 里，这里不做任何研究判断。
set -u

DIR=/data/deep-research/loop-mcp-semantics
STATE="$DIR/state"
mkdir -p "$STATE"

ENVELOPE=$(cat)
printf '%s\n' "$ENVELOPE" > "$STATE/last-envelope.json"
LOOP_ID=$(printf '%s' "$ENVELOPE" | /usr/bin/python3 -c \
  'import json,sys; d=json.load(sys.stdin); print((d.get("goal") or {}).get("id") or "")' 2>/dev/null)

complete_if_done() {
  [ -f "$STATE/DONE" ] || return 1
  if [ -n "$LOOP_ID" ]; then
    /usr/bin/python3 "$DIR/loop_complete.py" "$LOOP_ID" "$STATE/DONE" \
      >> "$STATE/complete.log" 2>&1
    echo "goal complete signalled: $LOOP_ID"
  else
    echo "DONE sentinel present but no loop_id in envelope" >&2
  fi
  return 0
}

# 上一 turn 已判收尾但 loop_complete 没成功（比如当时 loop-mcp 不可达）——先补这一步。
complete_if_done && exit 0

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
/home/uther/.local/bin/agent-run \
  --runtime claude \
  --route opus-5/native-ronny \
  --mcp-allow subagent \
  --mcp-allow katana-work-folder-mcp \
  --add-dir "$DIR" \
  --write \
  --timeout 240 \
  --json \
  --prompt-file "$DIR/coordinator-prompt.md" \
  > "$STATE/turn-$STAMP.json" 2> "$STATE/turn-$STAMP.err"
RC=$?

# stdout 只留一行诊断——loop-mcp 会把它存进 attempt 的 stdout_tail。
/usr/bin/python3 - "$STATE/turn-$STAMP.json" "$RC" <<'PY'
import json, sys
path, rc = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    lines = [ln.strip() for ln in (d.get("stdout") or "").splitlines() if ln.strip()]
    # charter 要求诊断行以 "turn:" 开头；退化时取最后一行非 code-fence 的内容
    diag = next((ln for ln in reversed(lines) if ln.startswith("turn:")), None)
    if diag is None:
        diag = next((ln for ln in reversed(lines) if not ln.startswith("```")),
                    "(no diagnostic line)")
    print(f"rc={rc} state={d.get('state')} exit={d.get('exit_code')} :: {diag}")
except Exception as exc:  # noqa: BLE001 — 诊断路径不该把 turn 判失败
    print(f"rc={rc} turn output unreadable: {exc}")
PY

complete_if_done
exit "$RC"
