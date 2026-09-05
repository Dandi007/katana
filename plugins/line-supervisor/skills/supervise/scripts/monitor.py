#!/usr/bin/env python3
"""monitor.py <line_id> — 给 Monitor 用的事件脚本：每条 stdout 是一次唤醒。

事件：① 线进入非 dd 驻停的 blocked / done / failed / fault / absent / probe-error；
② 看板 board:work-notes 出现本线（线 id 或本线派出的 dd 单号）的 question note 或 work.decision.v1；
③ autowake：本线的单到 awaiting_gate 超 GATE_LAG_S、线 unit 不在跑、调度器仍在
   no_progress backoff（streak>0）→ 把 streak 归零，让调度器下一 tick 点火（X-2 类探针缺陷的止血）。
健康的 dd 驻停（blocked + waiting_on=dd）不是事件。环境变量同 readings.sh。
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

for k in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)

LINE = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FG_LINE") or sys.exit("usage: monitor.py <line_id>")
STATE_URL = os.environ.get("FG_STATE_URL", "http://127.0.0.1:7494")
BUS_URL = os.environ.get("FG_BUS_URL", "http://127.0.0.1:7490")
TOKEN_FILE = os.environ.get("FG_BUS_TOKEN_FILE", "/data/agent-bus/tokens/uther-tui.token")
FG_ROOT = os.environ.get("FG_ROOT", "/data/fleet-graph")
STALL = f"{FG_ROOT}/runs/.scheduler/{LINE}.json"
GATE_LAG_S = int(os.environ.get("FG_GATE_LAG_S", 20 * 60))
POLL_S = int(os.environ.get("FG_POLL_S", 60))
TOKEN = open(TOKEN_FILE).read().strip() if os.path.exists(TOKEN_FILE) else ""


def get(url, auth=False):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"} if auth and TOKEN else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def line_state():
    try:
        d = get(f"{STATE_URL}/v1/lines")
        ls = d if isinstance(d, list) else d.get("lines", d)
        for l in ls:
            if l.get("folder_id") == LINE:
                wf = l.get("wake_facts") or {}
                # terminal 属于写它的那次 run；新一代的活 run 有不同 run_id ⇒ 终态是旧的
                stale = bool(l.get("terminal")) and wf.get("run_id") and l.get("run_id") and wf.get("run_id") != l.get("run_id")
                term = "running(stale-terminal)" if stale else l.get("terminal")
                return f"{term}|gen{l.get('generation')}|parked={l.get('parked')}|waiting_on={wf.get('waiting_on')}|dd={wf.get('dd_development_id') or ''}|reason={(wf.get('reason') or '')[:200]}"
        return "absent"
    except Exception as e:  # noqa: BLE001
        return f"probe-error:{type(e).__name__}"


def head_seq():
    try:
        return get(f"{BUS_URL}/v1/channels/board:work-notes/messages?limit=1", True).get("head_seq", 0)
    except Exception:  # noqa: BLE001
        return 0


def line_unit_active():
    try:
        out = subprocess.run(["systemctl", "--user", "list-units", f"fleet-graph-line-{LINE}*", "--no-legend", "--plain"], capture_output=True, text=True, timeout=10).stdout
        return "active" in out
    except Exception:  # noqa: BLE001
        return True  # fail closed：当它在跑，不动


def line_dev_ids():
    """本线派出的 dd 单号：看板 note 正文常只写单号不写线号（gate question 即如此），过滤要同时认这两种。"""
    ids = set()
    for rec in glob.glob(f"{FG_ROOT}/dd/*/record.json"):
        try:
            if json.load(open(rec)).get("dispatched_by") == LINE:
                ids.add(os.path.basename(os.path.dirname(rec)))
        except Exception:  # noqa: BLE001
            pass
    return ids


_woken = {}


def autowake():
    now = time.time()
    for rec in glob.glob(f"{FG_ROOT}/dd/*/record.json"):
        d = os.path.dirname(rec)
        try:
            if json.load(open(rec)).get("dispatched_by") != LINE:
                continue
            st = json.load(open(os.path.join(d, "status.json")))
            if st.get("state") != "awaiting_gate":
                continue
            dev = os.path.basename(d)
            # status.json 是查询缓存，mtime 会被读操作刷新；滞后以 events.jsonl 最后一条 acceptance success 为准
            gate_at = None
            for line in open(os.path.join(d, "events.jsonl")):
                try:
                    e = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if e.get("stage") == "acceptance" and e.get("event") == "success":
                    gate_at = e.get("at")
            if not gate_at:
                continue
            lag = now - time.mktime(time.strptime(gate_at, "%Y-%m-%dT%H:%M:%SZ")) + time.timezone
            if lag < GATE_LAG_S or now - _woken.get(dev, 0) < 1800 or line_unit_active():
                continue
            s = json.load(open(STALL))
            if int(s.get("streak") or 0) <= 0:
                continue
            s["streak"] = 0
            tmp = STALL + ".tmp"
            json.dump(s, open(tmp, "w"))
            os.replace(tmp, STALL)
            _woken[dev] = now
            print(f"autowake: {dev} awaiting_gate {int(lag/60)}min, line idle, streak reset 0 (gen {s.get('generation')})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"autowake-error: {type(e).__name__}: {e}", flush=True)


prev = None
last = head_seq()
while True:
    st = line_state()
    if st != prev:
        healthy_park = st.startswith("blocked|") and "waiting_on=dd|" in st
        if not healthy_park and any(k in st for k in ("blocked", "done", "failed", "fault", "absent", "probe-error")):
            print(f"{LINE} state: {st}", flush=True)
        prev = st
    try:
        d = get(f"{BUS_URL}/v1/channels/board:work-notes/messages?limit=50&after_seq={last}", True)
        devs = line_dev_ids()
        for m in d.get("messages", []):
            p = m.get("payload", {})
            t = json.dumps(p, ensure_ascii=False)
            mine = LINE in t or any(dev in t for dev in devs)
            if mine and (p.get("note_type") == "question" or m.get("kind") == "work.decision.v1"):
                print(f"board {m['channel_seq']} {m.get('sender_agent_id')} {m['kind']}/{p.get('note_type','')} decided_by={p.get('decided_by','')}: {(p.get('note') or p.get('question') or p.get('rationale') or '')[:300]}", flush=True)
        last = d.get("head_seq", last) or last
    except Exception:  # noqa: BLE001
        pass
    autowake()
    time.sleep(POLL_S)
