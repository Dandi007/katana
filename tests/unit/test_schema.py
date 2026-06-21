import sys, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness.schema import load_contract, ContractError

def _w(tmp, body):
    p = tmp/"x.contract.yaml"; p.write_text(body, encoding="utf-8"); return p

def test_three_axis_loads(tmp_path):
    c = load_contract(_w(tmp_path, """
skill: wiki:ingest
trigger: {prompt: "do it"}
expect:
  process: [{skill_loaded: wiki:ingest}]
  filesystem: [{created: out.md}]
  semantic: {rubric: r.md}
"""))
    assert c.skill == "wiki:ingest"
    assert c.process == [{"skill_loaded": "wiki:ingest"}]
    assert c.filesystem == [{"created": "out.md"}]
    assert c.semantic == {"rubric": "r.md"}

def test_invariant_requires_deterministic_anchor(tmp_path):
    # 只有 semantic、无 process/filesystem → 违反不变量
    with pytest.raises(ContractError, match="process.*filesystem"):
        load_contract(_w(tmp_path, """
skill: x:y
trigger: {prompt: "p"}
expect: {semantic: {rubric: r.md}}
"""))

def test_no_stdout_grep_type(tmp_path):
    with pytest.raises(ContractError, match="unknown.*assert|stdout_grep"):
        load_contract(_w(tmp_path, """
skill: x:y
trigger: {prompt: "p"}
expect: {process: [{stdout_grep: "foo"}]}
"""))

def test_model_explicit_default(tmp_path):
    c = load_contract(_w(tmp_path, """
skill: x:y
trigger: {prompt: p, model: lingzhi/claude-opus-4-8}
expect: {filesystem: [{created: a}]}
"""))
    assert c.model == "lingzhi/claude-opus-4-8"
