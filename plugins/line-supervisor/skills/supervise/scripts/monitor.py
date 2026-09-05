#!/usr/bin/env python3
"""monitor.py <line_id> — 给 Monitor 用的事件脚本：每条 stdout 是一次唤醒。

事件：① 线进入非 dd 驻停的 blocked / done / failed / fault / absent / probe-error；
② 看板 board:work-notes 出现本线（线 id 或本线派出的 dd 单号）的 question note 或 work.decision.v1；
③ autowake：本线的单到 awaiting_gate 或终态/interrupted（且无 active_unit）超 GATE_LAG_S、线 unit 不在跑、调度器仍在
   no_progress backoff（streak>0）→ 把 streak 归零，让调度器下一 tick 点火（X-2 类探针缺陷的止血）。
④ 调度器看门狗：fleet-graphd unit active 但 journal 超 SCHED_SILENT_S 无 folder_id 行（正常 tick ≈2.5 min）
   → 报事件，并把 py-spy dump（若可用）存到 FG_DUMP_DIR，供立案取栈；重启由监督者判断后手动做（X-6 类假死）。
健康的 dd 驻停（blocked + waiting_on=dd）不是事件。环境变量同 readings.sh。
"""
import glob
import json
import os
import shutil
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
SCHED_UNIT = os.environ.get("FG_SCHED_UNIT", "fleet-graphd")
SCHED_SILENT_S = int(os.environ.get("FG_SCHED_SILENT_S", 10 * 60))
DUMP_DIR = os.environ.get("FG_DUMP_DIR", os.path.expanduser("~/.local/state/line-supervisor"))


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


_sched_alerted_at = 0.0


def scheduler_watchdog():
    """fleet-graphd 活着但不 tick = 假死（2026-09-06 04:24–04:51 X-6 实例）。只报不重启。"""
    global _sched_alerted_at
    try:
        active = subprocess.run(["systemctl", "--user", "is-active", SCHED_UNIT], capture_output=True, text=True, timeout=10).stdout.strip()
        if active != "active":
            return  # 挂了是另一类事件：readings 会看到 unit 非 active
        out = subprocess.run(["journalctl", "--user", "-u", SCHED_UNIT, "--since", f"-{SCHED_SILENT_S}s", "--no-pager", "-o", "cat"], capture_output=True, text=True, timeout=20).stdout
        if '"folder_id"' in out:
            return
        if time.time() - _sched_alerted_at < 1800:
            return
        _sched_alerted_at = time.time()
        pid = subprocess.run(["systemctl", "--user", "show", SCHED_UNIT, "-p", "MainPID", "--value"], capture_output=True, text=True, timeout=10).stdout.strip()
        dump = ""
        if pid and pid != "0" and shutil.which("py-spy"):
            os.makedirs(DUMP_DIR, exist_ok=True)
            dump = os.path.join(DUMP_DIR, f"{SCHED_UNIT}-{pid}-{time.strftime('%Y%m%d-%H%M%S')}.pyspy")
            r = subprocess.run(["py-spy", "dump", "--pid", pid], capture_output=True, text=True, timeout=30)
            open(dump, "w").write(r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr else ""))
        print(f"scheduler-silent: {SCHED_UNIT} active (pid {pid}) but no tick in journal for >{SCHED_SILENT_S}s; stack dump: {dump or 'n/a'}; verify then `systemctl --user restart {SCHED_UNIT}`", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"scheduler-watchdog-error: {type(e).__name__}: {e}", flush=True)


_woken = {}


def autowake():
    now = time.time()
    for rec in glob.glob(f"{FG_ROOT}/dd/*/record.json"):
        d = os.path.dirname(rec)
        try:
            if json.load(open(rec)).get("dispatched_by") != LINE:
                continue
            st = json.load(open(os.path.join(d, "status.json")))
            state = st.get("state")
            # 需要线醒来接手的两类 dd 事实：到 gate（线自判）、到终态/被截断（线按 §5e 处置）。
            # 2026-09-06 04:11 R6 单 9000s 栅栏被截成 interrupted，线在 no_progress backoff 里睡了 45 min，
            # 旧版只认 awaiting_gate 没兜住 —— 所以终态也算。
            if state == "awaiting_gate":
                want = ("acceptance", "success")
            elif state in ("interrupted", "fault", "refused", "failed", "complete"):
                want = (None, None)
            else:
                continue
            dev = os.path.basename(d)
            # status.json 是查询缓存，mtime 会被读操作刷新；滞后以 events.jsonl 的对应事件时间为准
            gate_at = None
            for line in open(os.path.join(d, "events.jsonl")):
                try:
                    e = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if want[0] is None:
                    if e.get("event") in ("failed", "fault", "refused", "success") and e.get("stage"):
                        gate_at = e.get("at")
                elif e.get("stage") == want[0] and e.get("event") == want[1]:
                    gate_at = e.get("at")
            if not gate_at:
                continue
            # 已被线接手的终态不催：active_unit 非空（re-adopt 后 r2 在跑）说明线已处理
            if state != "awaiting_gate" and st.get("active_unit"):
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
            print(f"autowake: {dev} {state} {int(lag/60)}min, line idle, streak reset 0 (gen {s.get('generation')})", flush=True)
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
            # 归属判定：本线派出的单号出现在正文，或裁决的 decided_by 就是本线。
            # 不用「LINE 子串出现在任何字段」——别的线在 rationale 里引用「同舰先例 wf-xxx」会误报
            # （2026-09-06 01:43 看板 2893/2894 实例）。
            mine_dev = any(dev in t for dev in devs)
            is_decision = m.get("kind") == "work.decision.v1"
            is_question = p.get("note_type") == "question"
            mine = mine_dev or (is_decision and p.get("decided_by") == LINE) or (is_question and LINE in str(p.get("note") or ""))
            if mine and (is_question or is_decision):
                print(f"board {m['channel_seq']} {m.get('sender_agent_id')} {m['kind']}/{p.get('note_type','')} decided_by={p.get('decided_by','')}: {(p.get('note') or p.get('question') or p.get('rationale') or '')[:300]}", flush=True)
        last = d.get("head_seq", last) or last
    except Exception:  # noqa: BLE001
        pass
    autowake()
    scheduler_watchdog()
    time.sleep(POLL_S)
