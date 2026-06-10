import textwrap
import pytest
from harness.schema import load_contract, ContractError


def write(tmp_path, body):
    p = tmp_path / "query-hot.contract.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_minimal_contract_defaults(tmp_path):
    p = write(tmp_path, """\
        skill: wiki:query
        input:
          prompt: "用 /wiki:query 回答：手冲咖啡的萃取温度？"
        assert:
          - stdout_grep: "\\\\[\\\\["
    """)
    c = load_contract(p)
    assert c.skill == "wiki:query"
    assert c.case_id == "query-hot"
    assert c.cwd == "kb"
    assert c.model == "lingzhi/claude-opus-4-8"
    assert c.permission_mode == "acceptEdits"
    assert c.timeout == 600
    assert c.requires == []
    assert c.asserts == [{"stdout_grep": "\\[\\["}]
    assert c.verdict is None


def test_missing_skill_rejected(tmp_path):
    p = write(tmp_path, """\
        input: {prompt: hi}
        assert: [{stdout_grep: x}]
    """)
    with pytest.raises(ContractError, match="skill"):
        load_contract(p)


def test_needs_assert_or_verdict(tmp_path):
    p = write(tmp_path, """\
        skill: a:b
        input: {prompt: hi}
    """)
    with pytest.raises(ContractError, match="assert .*verdict"):
        load_contract(p)


def test_unknown_assert_type_rejected(tmp_path):
    p = write(tmp_path, """\
        skill: a:b
        input: {prompt: hi}
        assert: [{regex_match: x}]
    """)
    with pytest.raises(ContractError, match="regex_match"):
        load_contract(p)


def test_verdict_only_contract_ok(tmp_path):
    p = write(tmp_path, """\
        skill: deep-research:deep-research
        input: {prompt: 研究一下}
        verdict:
          rubric: case-rubrics/deep-research.md
          inputs: ["{cwd}/report.md"]
    """)
    c = load_contract(p)
    assert c.asserts == [] and c.verdict["rubric"].endswith("deep-research.md")


def test_bad_allowed_tools_rejected(tmp_path):
    p = write(tmp_path, """\
        skill: a:b
        input: {prompt: hi}
        run: {allowed_tools: "Read,Write"}
        assert: [{stdout_grep: x}]
    """)
    with pytest.raises(ContractError, match="allowed_tools"):
        load_contract(p)


def test_verdict_missing_rubric_rejected(tmp_path):
    p = write(tmp_path, """\
        skill: a:b
        input: {prompt: hi}
        verdict: {inputs: ["x"]}
    """)
    with pytest.raises(ContractError, match="rubric"):
        load_contract(p)


def test_turns_contract_parsed(tmp_path):
    p = write(tmp_path, """\
        skill: incubate:incubate
        input:
          turns:
            - "开个孵化台"
            - "capture 我的想法"
        assert:
          - file_exists: "{cwd}/Incubator"
    """)
    c = load_contract(p)
    assert c.turns == ["开个孵化台", "capture 我的想法"]
    assert c.prompt == ""

def test_turns_and_prompt_both_rejected(tmp_path):
    p = write(tmp_path, """\
        skill: a:b
        input:
          prompt: hi
          turns: ["x"]
        assert: [{stdout_grep: x}]
    """)
    with pytest.raises(ContractError, match="prompt.*turns|turns.*prompt"):
        load_contract(p)

def test_empty_turns_rejected(tmp_path):
    p = write(tmp_path, """\
        skill: a:b
        input: {turns: []}
        assert: [{stdout_grep: x}]
    """)
    with pytest.raises(ContractError, match="turns"):
        load_contract(p)
