from pathlib import Path
from harness.schema import Contract
from harness.case import check_requires, run_case

SHIM = str(Path(__file__).parent / "fake-claude")

def make_contract(tmp_path, **kw):
    d = dict(skill="wiki:query", prompt="问个问题", path=tmp_path / "q.contract.yaml",
             case_id="q", asserts=[{"stdout_grep": "OK"}])
    d.update(kw)
    return Contract(**d)

def golden(tmp_path):
    g = tmp_path / "golden"
    (g / "kb").mkdir(parents=True); (g / "claude-config").mkdir()
    (g / "kb" / "seed.md").write_text("seed")
    return g

def test_check_requires_env_and_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XHS_PROFILE", raising=False)
    assert check_requires(["env:XHS_PROFILE"]) is not None     # 不满足 → 原因字符串
    monkeypatch.setenv("XHS_PROFILE", str(tmp_path))
    assert check_requires(["env:XHS_PROFILE"]) is None
    assert check_requires([f"dir:{tmp_path}"]) is None
    assert check_requires(["dir:/nonexistent/zz"]) is not None
    assert check_requires(["exclusive:chrome"]) is None        # exclusive 不检查

def test_run_case_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "all OK")
    r = run_case(make_contract(tmp_path), golden(tmp_path), tmp_path / "work",
                 claude_bin=SHIM, base_env={})
    assert r.status == "PASS" and r.attempts == 1
    # 快照隔离：case 目录有独立 kb 副本
    assert (tmp_path / "work" / "q" / "kb" / "seed.md").exists()

def test_run_case_skip(tmp_path):
    c = make_contract(tmp_path, requires=["dir:/nonexistent/zz"])
    r = run_case(c, golden(tmp_path), tmp_path / "work", claude_bin=SHIM, base_env={})
    assert r.status == "SKIP" and "nonexistent" in r.detail

def test_run_case_retry_then_fail_keeps_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "nope")
    r = run_case(make_contract(tmp_path), golden(tmp_path), tmp_path / "work",
                 claude_bin=SHIM, base_env={})
    assert r.status == "FAIL" and r.attempts == 2 and r.attribution == "unknown"
    assert Path(r.kept_dir).exists()

def test_timeout_attributed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
    c = make_contract(tmp_path, timeout=1)
    r = run_case(c, golden(tmp_path), tmp_path / "work", claude_bin=SHIM, base_env={})
    assert r.status == "FAIL" and r.attribution == "env"
