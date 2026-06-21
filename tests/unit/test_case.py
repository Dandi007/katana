"""test_case.py — v2 run_case 六步编排单测。

使用新三轴 schema Contract + fake-claude，
验证 PASS/SKIP/FAIL/retry/超时/多轮等核心路径。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from harness.schema import load_contract, Contract
import runner as runner_mod
from runner import run_case, _check_requires

SHIM = str(Path(__file__).parent / "fake-claude")


# ──────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────

def write_contract(tmp_path, body: str) -> Path:
    p = tmp_path / "c.contract.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def make_contract(tmp_path, **overrides):
    """构造最小合法 v2 Contract（process 轴：skill_loaded fake-skill）。"""
    body = """
skill: demo:hello
trigger:
  prompt: "hi"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
"""
    p = write_contract(tmp_path, body)
    c = load_contract(p)
    # 应用覆盖（直接替换 dataclass 字段）
    for k, v in overrides.items():
        object.__setattr__(c, k, v)
    return c


def golden(tmp_path):
    """构造最小 golden 目录（含 kb 和 claude-config）。"""
    g = tmp_path / "golden"
    (g / "kb").mkdir(parents=True)
    (g / "claude-config").mkdir()
    (g / "kb" / "seed.md").write_text("seed")
    return g


EMPTY_MODELS = {}   # skip-judge 模式：不跑轴③


# ──────────────────────────────────────────────────
# _check_requires 测试（v1 兼容）
# ──────────────────────────────────────────────────

def test_check_requires_env_and_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XHS_PROFILE", raising=False)
    assert _check_requires(["env:XHS_PROFILE"]) is not None
    monkeypatch.setenv("XHS_PROFILE", str(tmp_path))
    assert _check_requires(["env:XHS_PROFILE"]) is None
    assert _check_requires([f"dir:{tmp_path}"]) is None
    assert _check_requires(["dir:/nonexistent/zz"]) is not None
    assert _check_requires(["exclusive:chrome"]) is None   # exclusive 不检查


def test_unknown_requires_kind_raises(tmp_path):
    """未知 requires kind 不 raise，仅跳过（v2 行为：_check_requires 不 raise 未知 kind）。"""
    # v2 _check_requires 对未知 kind 静默跳过（与 v1 check_requires 不同）
    result = _check_requires(["envv:TYPO"])
    assert result is None   # 未知 kind 忽略，不报错


# ──────────────────────────────────────────────────
# run_case 核心路径
# ──────────────────────────────────────────────────

def test_run_case_pass(tmp_path, monkeypatch):
    """轴① skill_loaded=fake-skill：fake-claude 吐 Skill trace → PASS。"""
    c = make_contract(tmp_path)
    r = run_case(c, golden(tmp_path), tmp_path / "work",
                 base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r.status == "PASS", r.detail
    assert r.attempts == 1
    assert r.case_dir.endswith("/c")
    # 快照隔离：case 目录有独立 kb 副本
    assert (tmp_path / "work" / "c" / "kb" / "seed.md").exists()


def test_run_case_skip(tmp_path):
    """requires dir 不存在 → SKIP。"""
    c = make_contract(tmp_path)
    object.__setattr__(c, "requires", ["dir:/nonexistent/zz"])
    r = run_case(c, golden(tmp_path), tmp_path / "work",
                 base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r.status == "SKIP" and "nonexistent" in r.detail


def test_run_case_retry_then_fail_keeps_dir(tmp_path, monkeypatch):
    """断言失败 → retry → FAIL；kept_dir 存在。"""
    # 契约要求 skill_loaded=absent-skill，fake-claude 只吐 fake-skill → FAIL
    body = """
skill: demo:hello
trigger:
  prompt: "hi"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: absent-skill
"""
    p = write_contract(tmp_path, body)
    c = load_contract(p)
    r = run_case(c, golden(tmp_path), tmp_path / "work",
                 base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r.status == "FAIL", r.detail
    assert r.attempts == 2
    assert r.attribution == "unknown"
    assert Path(r.kept_dir).exists()


def test_timeout_attributed_env(tmp_path, monkeypatch):
    """超时两次 → FAIL / attribution=env。"""
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
    c = make_contract(tmp_path)
    object.__setattr__(c, "timeout", 1)
    r = run_case(c, golden(tmp_path), tmp_path / "work",
                 base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r.status == "FAIL" and r.attribution == "env"


def test_snapshot_rerun_same_workroot_is_fresh(tmp_path, monkeypatch):
    """同一 work_root 重跑：第二次必须是全新快照，不得嵌套/残留。"""
    c = make_contract(tmp_path)
    g = golden(tmp_path)
    run_case(c, g, tmp_path / "work",
             base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    # 在 case 目录留一个脏文件
    (tmp_path / "work" / "c" / "kb" / "dirty.md").write_text("leftover")
    run_case(c, g, tmp_path / "work",
             base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert not (tmp_path / "work" / "c" / "kb" / "dirty.md").exists()   # 脏文件被清
    assert not (tmp_path / "work" / "c" / "golden").exists()            # 无嵌套
    assert (tmp_path / "work" / "c" / "kb" / "seed.md").exists()


def test_run_case_multi_turn(tmp_path, monkeypatch):
    """多轮（turns）路径：filesystem 验 created 文件。"""
    monkeypatch.setenv("FAKE_CLAUDE_APPEND", "turns.log")
    body = """
skill: demo:hello
trigger:
  turns:
    - "x"
    - "y"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
"""
    p = write_contract(tmp_path, body)
    c = load_contract(p)
    r = run_case(c, golden(tmp_path), tmp_path / "work",
                 base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r.status == "PASS", r.detail
    turns_log = tmp_path / "work" / "c" / "kb" / "turns.log"
    assert turns_log.exists()
    assert turns_log.read_text().splitlines() == ["x", "y"]
