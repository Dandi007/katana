#!/usr/bin/env python3
"""直接从各 agent-run 的 opencode.db 汇总最近 N 小时 token，按单 / 线分组。

绕过 metricsd 的 usage_source=missing（agent-run conformance retry 后 run_dir 指向
无会话库的新 stamp 目录）。用法：opencode-tokens.py [hours=1] [fleet_root=/data/fleet-graph]
"""
import glob
import json
import os
import sqlite3
import sys
import time

hours = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
root = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("FG_ROOT", "/data/fleet-graph")
cut = time.time() - hours * 3600
tot = {"input": 0, "output": 0, "cache_read": 0, "msgs": 0, "dbs": 0}
per = {}
pattern = "/agent-runs/*/*/home/opencode/data/opencode/opencode.db"
for db in glob.glob(f"{root}/dd/*{pattern}") + glob.glob(f"{root}/runs/*{pattern}"):
    if os.path.getmtime(db) < cut:
        continue
    try:
        c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
        rows = c.execute("select data from message where json_extract(data,'$.role')='assistant'").fetchall()
    except Exception as e:  # noqa: BLE001 — 只读汇总，坏库跳过
        print("ERR", db[-70:], e)
        continue
    tot["dbs"] += 1
    key = db.split("/agent-runs/")[0].split("/")[-1]
    p = per.setdefault(key, {"input": 0, "output": 0, "cache_read": 0})
    for (d,) in rows:
        j = json.loads(d)
        t = j.get("tokens") or {}
        if (j.get("time") or {}).get("created", 0) / 1000 < cut:
            continue
        tot["msgs"] += 1
        for k, v in (("input", t.get("input", 0)), ("output", t.get("output", 0)), ("cache_read", (t.get("cache") or {}).get("read", 0))):
            v = v or 0
            tot[k] += v
            p[k] += v
print(f"last {hours}h from opencode.db:", tot)
for k, v in sorted(per.items(), key=lambda kv: -kv[1]["output"]):
    print("  ", k, v)
