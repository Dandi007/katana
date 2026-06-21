"""test_runner.py — v2 run_case 六步编排单测（Task 9）。

用 fake-claude 跑三轴契约：
  - 轴① process: skill_loaded fake-skill（fake-claude 吐 Skill trace）
  - 轴② filesystem: created out.md（FAKE_CLAUDE_WRITE 写入）
  断言 run_case 返回 PASS、axis_detail 捕获 created 文件。

另测 _resolve_verdict_inputs 的 {case_trace} / created 占位符。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.schema import load_contract
from runner import CaseResult, run_case, _resolve_verdict_inputs, _check_requires

SHIM = str(Path(__file__).parent / "fake-claude")
EMPTY_MODELS = {}   # skip-judge 路径：无轴③


# ──────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────

def write_contract(tmp_path, body: str) -> Path:
    p = tmp_path / "case.contract.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def golden_dir(tmp_path) -> Path:
    """构造最小 golden 目录（kb + claude-config）。"""
    g = tmp_path / "golden"
    (g / "kb").mkdir(parents=True)
    (g / "claude-config").mkdir()
    (g / "kb" / "seed.md").write_text("seed content")
    return g


# ──────────────────────────────────────────────────
# 核心路径：三轴契约 PASS
# ──────────────────────────────────────────────────

def test_run_case_three_axis_pass(tmp_path, monkeypatch):
    """fake-claude 吐 Skill trace + 写 out.md → 轴①②全过 → PASS，delta 捕获 created。"""
    # FAKE_CLAUDE_WRITE 让 fake-claude 在 cwd 写 out.md
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", "out.md:hello world")

    contract_yaml = """
skill: demo:hello
trigger:
  prompt: "write out.md"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
  filesystem:
    - created: out.md
"""
    p = write_contract(tmp_path, contract_yaml)
    contract = load_contract(p)

    r = run_case(
        contract,
        golden_dir(tmp_path),
        tmp_path / "work",
        base_env={},
        models=EMPTY_MODELS,
        claude_bin=SHIM,
    )

    assert r.status == "PASS", f"expected PASS, got {r.status}: {r.detail}"
    assert r.attempts == 1
    assert r.model == "lingzhi/claude-opus-4-8"

    # axis_detail 记录了 delta.created
    delta = r.axis_detail.get("delta", {})
    assert "out.md" in delta.get("created", []), \
        f"expected 'out.md' in created, got: {delta}"

    # axis_detail 记录了轴①②结果
    proc = r.axis_detail.get("process", [])
    assert any(x["type"] == "skill_loaded" and x["ok"] for x in proc)

    fs = r.axis_detail.get("filesystem", [])
    assert any(x["type"] == "created" and x["ok"] for x in fs)


def test_run_case_fails_when_file_not_created(tmp_path):
    """fake-claude 不写文件 → filesystem created 断言失败 → FAIL。"""
    # 不设 FAKE_CLAUDE_WRITE，fake-claude 只吐 trace，不写文件
    contract_yaml = """
skill: demo:hello
trigger:
  prompt: "write something"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
  filesystem:
    - created: expected-output.md
"""
    p = write_contract(tmp_path, contract_yaml)
    contract = load_contract(p)

    r = run_case(
        contract,
        golden_dir(tmp_path),
        tmp_path / "work",
        base_env={},
        models=EMPTY_MODELS,
        claude_bin=SHIM,
    )

    assert r.status == "FAIL", f"expected FAIL, got {r.status}"
    assert "created" in r.detail or "fs/" in r.detail
    assert r.attempts == 1  # I1: 轴②FAIL 不重试（防 C1 回退）


def test_run_case_fails_when_skill_not_loaded(tmp_path):
    """契约要求 skill_loaded=absent-skill，fake-claude 只吐 fake-skill → 轴① FAIL。"""
    contract_yaml = """
skill: demo:hello
trigger:
  prompt: "hello"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: absent-skill
"""
    p = write_contract(tmp_path, contract_yaml)
    contract = load_contract(p)

    r = run_case(
        contract,
        golden_dir(tmp_path),
        tmp_path / "work",
        base_env={},
        models=EMPTY_MODELS,
        claude_bin=SHIM,
    )

    assert r.status == "FAIL"
    assert r.attempts == 1   # C1: 轴①FAIL 不重试，立即返回


def test_run_case_skip_when_requires_not_met(tmp_path):
    """requires dir 不存在 → SKIP（不运行 claude）。"""
    contract_yaml = """
skill: demo:hello
setup:
  requires:
    - dir:/nonexistent/zzz
trigger:
  prompt: "hi"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
"""
    p = write_contract(tmp_path, contract_yaml)
    contract = load_contract(p)

    r = run_case(
        contract,
        golden_dir(tmp_path),
        tmp_path / "work",
        base_env={},
        models=EMPTY_MODELS,
        claude_bin=SHIM,
    )

    assert r.status == "SKIP"
    assert "nonexistent" in r.detail


def test_run_case_isolation(tmp_path, monkeypatch):
    """每次 run_case 得到全新 fixture 副本：脏文件不会跨次残留。"""
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", "dirty.md:polluted")

    contract_yaml = """
skill: demo:hello
trigger:
  prompt: "write dirty"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
  filesystem:
    - created: dirty.md
"""
    p = write_contract(tmp_path, contract_yaml)
    contract = load_contract(p)
    g = golden_dir(tmp_path)

    # 第一次跑，写了 dirty.md
    r1 = run_case(contract, g, tmp_path / "work",
                  base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r1.status == "PASS"

    # 不写文件的第二次跑（清除 env）
    monkeypatch.delenv("FAKE_CLAUDE_WRITE", raising=False)

    # 第二次断言不 created，验证隔离（fresh clone，dirty.md 不存在）
    contract_yaml2 = """
skill: demo:hello
trigger:
  prompt: "just trace"
  model: lingzhi/claude-opus-4-8
expect:
  process:
    - skill_loaded: fake-skill
"""
    p2 = tmp_path / "c2.contract.yaml"
    p2.write_text(contract_yaml2, encoding="utf-8")
    contract2 = load_contract(p2)
    r2 = run_case(contract2, g, tmp_path / "work",
                  base_env={}, models=EMPTY_MODELS, claude_bin=SHIM)
    assert r2.status == "PASS"
    # 第二次 run 的 case 目录里不应有 dirty.md（隔离干净）
    case2_dir = Path(r2.case_dir)
    assert not (case2_dir / "kb" / "dirty.md").exists()


# ──────────────────────────────────────────────────
# _resolve_verdict_inputs 占位符测试
# ──────────────────────────────────────────────────

def test_resolve_verdict_inputs_case_trace():
    """{case_trace} 占位符展开为 case_root/case.trace.jsonl。"""
    paths = _resolve_verdict_inputs(
        ["{case_trace}"],
        Path("/some/case"),
        "kb",
    )
    assert len(paths) == 1
    assert str(paths[0]) == "/some/case/case.trace.jsonl"


def test_resolve_verdict_inputs_cwd():
    """{cwd} 占位符展开为 case_root/<fixture>/<rel>。"""
    paths = _resolve_verdict_inputs(
        ["{cwd}/result.md"],
        Path("/case"),
        "kb",
    )
    assert str(paths[0]) == "/case/kb/result.md"


def test_resolve_verdict_inputs_created_expands_delta():
    """`created` 占位符展开为 delta.created 中所有文件的完整路径。"""
    delta_info = {
        "created": {"a.md", "b.md"},
        "modified": set(),
        "deleted": set(),
    }
    paths = _resolve_verdict_inputs(
        ["created"],
        Path("/case"),
        "kb",
        delta_info,
    )
    names = {p.name for p in paths}
    assert names == {"a.md", "b.md"}
    # 路径包含 fixture 子目录
    assert all(str(p).startswith("/case/kb/") for p in paths)


def test_resolve_verdict_inputs_created_no_delta():
    """`created` 占位符在 delta_info=None 时返回空列表（无 delta 信息）。"""
    paths = _resolve_verdict_inputs(["created"], Path("/case"), "kb", None)
    assert paths == []


def test_resolve_verdict_inputs_mixed():
    """混合占位符：{case_trace} + created + 字面路径。"""
    delta_info = {"created": {"out.md"}, "modified": set(), "deleted": set()}
    paths = _resolve_verdict_inputs(
        ["{case_trace}", "created", "{cwd}/notes.md"],
        Path("/c"),
        "kb",
        delta_info,
    )
    strs = [str(p) for p in paths]
    assert "/c/case.trace.jsonl" in strs
    assert any("out.md" in s for s in strs)
    assert "/c/kb/notes.md" in strs


# ──────────────────────────────────────────────────
# C2：未知 requires kind 必须 raise
# ──────────────────────────────────────────────────

def test_unknown_requires_kind_raises():
    """typo kind（如 envv:FOO）必须 raise ValueError，不能静默当满足条件（防假 PASS）。"""
    with pytest.raises(ValueError, match="unknown requires kind"):
        _check_requires(["envv:MY_VAR"])
