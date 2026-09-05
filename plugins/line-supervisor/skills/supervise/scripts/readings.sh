#!/usr/bin/env bash
# readings.sh <line_id> [board_after_seq] — 一次拉全「监督一条线」的六个读数。
# 只读；本机回环请求自动去代理。环境变量可覆盖默认部署位置。
set -uo pipefail
LINE="${1:?usage: readings.sh <line_id> [board_after_seq]}"
AFTER="${2:-0}"
STATE_URL="${FG_STATE_URL:-http://127.0.0.1:7494}"
BUS_URL="${FG_BUS_URL:-http://127.0.0.1:7490}"
BUS_TOKEN_FILE="${FG_BUS_TOKEN_FILE:-/data/agent-bus/tokens/uther-tui.token}"
FG_ROOT="${FG_ROOT:-/data/fleet-graph}"
APP_ROOT="${FG_APP_ROOT:-/data/apps/fleet-graph}"
PROM_URL="${FG_PROM_URL:-http://127.0.0.1:9090}"
UNITS="${FG_UNITS:-fleet-graphd fleet-graph-dd-mcp fleet-graph-goal-mcp fleet-graph-decision-mcp fleet-graph-decision-bridge fleet-graph-state fleet-graph-research-mcp}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
noproxy() { env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy "$@"; }

date "+%Y-%m-%d %H:%M"

echo "== ① 线活着"
noproxy curl -s -m 10 "$STATE_URL/v1/lines" | python3 -c '
import json,sys
line=sys.argv[1]; d=json.load(sys.stdin); ls=d if isinstance(d,list) else d.get("lines",d.get("items",[]))
for l in ls:
    if l.get("folder_id")==line:
        w=l.get("wake_facts") or {}
        print("gen",l.get("generation"),"round",l.get("round"),"phase",l.get("phase"),"terminal",l.get("terminal"),"parked",l.get("parked"),"hb_s",round(l.get("heartbeat_age_s") or 0),"waiting_on",w.get("waiting_on"),"dd",w.get("dd_development_id"),"stale",l.get("wake_facts_stale"),"release",l.get("release_id"))
        print("reason:",(w.get("reason") or "")[:300]); break
else: print("ABSENT")' "$LINE" 2>&1
systemctl --user list-units "fleet-graph-line-${LINE}*" --no-pager --no-legend 2>/dev/null | awk '{print "unit",$1,$3,$4}'

echo "== ② 在推进（rounds 最近两轮；progress 请经 work-folder MCP fs_read 尾部）"
tail -n 2 "$FG_ROOT/runs/$LINE/rounds.jsonl" 2>/dev/null | cut -c1-300
python3 - "$FG_ROOT/runs/.scheduler/$LINE.json" <<'EOF' 2>/dev/null
import json,sys,time
d=json.load(open(sys.argv[1]))
print("stall:",{k:d.get(k) for k in ("generation","streak","line_state","parked_dd_development_id","parked_at")},"last_start",time.strftime("%H:%M",time.localtime(d.get("last_start_at") or 0)))
EOF
journalctl --user -u fleet-graphd --since "-15min" --no-pager 2>/dev/null | grep "\"$LINE\"" | tail -n 1 | grep -o '"refusal": "[^"]*", "detail": "[^"]\{0,120\}'

echo "== ③ 单子（dispatched_by=$LINE）"
for d in "$FG_ROOT"/dd/*/; do python3 - "$d" "$LINE" <<'EOF'
import json,sys,os,time
d,line=sys.argv[1],sys.argv[2]; p=d+"/record.json"
if not os.path.exists(p): sys.exit()
r=json.load(open(p))
if r.get("dispatched_by")!=line: sys.exit()
st=json.load(open(d+"/status.json")) if os.path.exists(d+"/status.json") else {}
dev=os.path.basename(d.rstrip("/"))
if st.get("state")=="complete": print(dev,"complete"); sys.exit()
last=""
try:
    ev=[json.loads(l) for l in open(d+"/events.jsonl") if l.strip()]
    ev=[e for e in ev if e.get("stage")]
    if ev: e=ev[-1]; last=f'{e.get("at","")[11:16]}Z {e.get("stage")} {e.get("event")}'
except Exception: pass
print(dev,"| created",r.get("created_at","")[5:16],"| state",st.get("state"),"| stage",st.get("stage"),"| awaiting",bool(st.get("awaiting")),"| fail",(st.get("failure") or {}).get("code"),"| unit",st.get("active_unit") or "-","| last_event",last)
EOF
done

echo "== ④ 看板 board:work-notes after_seq=$AFTER（过滤 $LINE）"
if [ -r "$BUS_TOKEN_FILE" ]; then
  noproxy curl -s -m 10 -H "Authorization: Bearer $(cat "$BUS_TOKEN_FILE")" "$BUS_URL/v1/channels/board:work-notes/messages?limit=50&after_seq=$AFTER" | python3 -c '
import json,sys
line=sys.argv[1]; d=json.load(sys.stdin); print("head_seq",d.get("head_seq"))
for m in d.get("messages",d.get("items",[])):
    s=json.dumps(m,ensure_ascii=False)
    if line in s:
        p=m.get("payload",{}); print(m.get("channel_seq"),m.get("kind"),m.get("sender_agent_id"),(m.get("created_at") or "")[11:16],p.get("note_type") or p.get("decision"),p.get("decided_by",""),str(p.get("note") or p.get("rationale") or "")[:140].replace("\n"," "))' "$LINE"
else
  echo "no bus token at $BUS_TOKEN_FILE"
fi

echo "== ⑤ 生产面"
for u in $UNITS; do printf '%s %s %s\n' "$u" "$(systemctl --user is-active "$u" 2>/dev/null)" "$(systemctl --user show "$u" -p ActiveEnterTimestamp --value 2>/dev/null)"; done
echo "current -> $(readlink -f "$APP_ROOT/current" 2>/dev/null) sha=$(cat "$APP_ROOT/current/.release-sha" 2>/dev/null)"

echo "== ⑥ 花钱"
python3 "$HERE/opencode-tokens.py" 1 2>/dev/null || echo "opencode-tokens.py failed"
python3 - "$PROM_URL" <<'EOF' 2>/dev/null
import json,urllib.request,urllib.parse,os,sys
for k in list(os.environ):
    if k.lower().endswith("_proxy"): os.environ.pop(k)
base=sys.argv[1]
for q in ("cost_obs:idle_noop_rate:ratio","cost_obs:rework_tax:ratio","cost_obs:sunk_cost_rate:ratio","cost_obs:management_execution:ratio",'sum(increase(agent_runtime_tokens_output_total{runtime="opencode"}[1h]))'):
    try:
        d=json.load(urllib.request.urlopen(base+"/api/v1/query?"+urllib.parse.urlencode({"query":q}),timeout=8))
        print(q[:60],"=>",[round(float(r["value"][1]),4) for r in d["data"]["result"]][:3])
    except Exception as e: print(q[:60],"=> ERR",type(e).__name__)
EOF
