#!/usr/bin/env bash
# 读 panel-meta.json：恰 4 个不同模型；opus 路 base_url 空(真原生)；其余指 15721。
# 只验路由保真，不验 exit 成功——harness 隔离下 opus auth 会挂属预期。
set -uo pipefail
META="$CWD/scratch/.jury/panel-meta.json"
[ -f "$META" ] || { echo "no panel-meta.json"; exit 1; }
python3 - "$META" <<'PY'
import json, sys
meta = json.loads(open(sys.argv[1]).read())
names = {m["name"] for m in meta}
assert names == {"opus","gpt","deepseek","qwen"}, f"roster={names}"
opus = next(m for m in meta if m["name"]=="opus")
assert opus["base_url_used"] == "", f"opus 不该走 ccs: {opus['base_url_used']}"
for n in ("gpt","deepseek","qwen"):
    m = next(x for x in meta if x["name"]==n)
    assert "15721" in m["base_url_used"], f"{n} 没走 ccs: {m['base_url_used']}"
print("fanout fidelity OK")
PY
