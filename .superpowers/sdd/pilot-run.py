#!/usr/bin/env -S uv run --with pyyaml
"""一次性试点验证：直接跑 3 个迁移契约过完整 run_case，绕开 discover（37 旧契约还没迁）。"""
import sys, tempfile, os
from pathlib import Path
REPO = Path("/Volumes/Data/code/worktrees/katana/e2e-v2")
sys.path.insert(0, str(REPO / "tests"))
from harness.schema import load_contract
from harness import isolate, model
import runner

CONTRACTS = [
    "plugins/wiki/tests/contracts/ingest-inbox.contract.yaml",
    "plugins/retrieval/tests/contracts/search-note-local.contract.yaml",
    "plugins/work-folder/tests/contracts/checkpoint-save.contract.yaml",
]
cs = [load_contract(REPO / c) for c in CONTRACTS]
plugins = {c.path.parts[-4] for c in cs}
tmp = Path(tempfile.mkdtemp(prefix="pilot."))
base_env = isolate.build_base_env(no_ccs_check=False)
# ccs API-key 模式
base_env.setdefault("ANTHROPIC_BASE_URL", "http://127.0.0.1:15721")
base_env.setdefault("ANTHROPIC_API_KEY", "ccs-local")
models = model.load_models(REPO)
print(f"golden setup: plugins={sorted(plugins)} tmp={tmp}", flush=True)
golden = isolate.golden_setup(REPO, tmp, plugins, claude_bin="claude")
for c in cs:
    r = runner.run_case(c, golden, tmp / "cases", base_env, models, claude_bin="claude")
    ad = getattr(r, "axis_detail", {}) or {}
    print(f"\n=== {c.skill}#{c.case_id}: {r.status} (attempts={r.attempts}, {r.duration_s:.0f}s) ===", flush=True)
    for axis in ("process", "fs"):
        for res in ad.get(axis, []):
            mark = "✓" if res.get("ok") else "✗"
            print(f"  [{axis}] {mark} {res.get('type')}: {res.get('detail','')}", flush=True)
    if r.status == "FAIL":
        print(f"  detail: {r.detail}", flush=True)
print(f"\nwork dir: {tmp}", flush=True)
