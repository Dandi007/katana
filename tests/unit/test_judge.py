"""轴③ judge 单元测试（v2）。

走 get_judge("single").judge(...) 接口；加 jury stub 测试。
fake-claude shim 通过 FAKE_CLAUDE_STDOUT env 控制输出。
"""
import sys
import pathlib
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness.judge import get_judge, parse_verdict_json

FAKE = str(pathlib.Path(__file__).resolve().parent / "fake-claude")


# ──────────────────────────────────────────────────
# parse_verdict_json
# ──────────────────────────────────────────────────

def test_parse_fenced_json():
    """从混合文本中提取 fenced json。"""
    txt = '前言\n```json\n{"items": [{"q": "x", "answer": "yes", "evidence": "e"}]}\n```\n尾巴'
    v = parse_verdict_json(txt)
    assert v["items"][0]["answer"] == "yes"


# ──────────────────────────────────────────────────
# SingleJudge via get_judge("single")
# ──────────────────────────────────────────────────

def test_case_verdict_all_yes_passes(tmp_path, monkeypatch):
    """全部 yes 返回 PASS。"""
    rubric = tmp_path / "r.md"
    rubric.write_text("1. 有引用吗？")
    artifact = tmp_path / "report.md"
    artifact.write_text("内容 [[引用]]")
    monkeypatch.setenv(
        "FAKE_CLAUDE_STDOUT",
        '```json\n{"items": [{"q": "有引用吗？", "answer": "yes", "evidence": "[[引用]]"}]}\n```',
    )
    status, result = get_judge("single").judge(
        rubric=rubric,
        inputs=[artifact],
        model="m",
        work_dir=tmp_path,
        env={},
        claude_bin=FAKE,
    )
    assert status == "PASS"
    assert result["items"][0]["answer"] == "yes"


def test_case_verdict_any_no_needs_review(tmp_path, monkeypatch):
    """任何 no 返回 NEEDS-REVIEW。"""
    rubric = tmp_path / "r.md"
    rubric.write_text("q")
    monkeypatch.setenv(
        "FAKE_CLAUDE_STDOUT",
        '```json\n{"items": [{"q": "q", "answer": "no", "evidence": "缺"}]}\n```',
    )
    status, result = get_judge("single").judge(
        rubric=rubric,
        inputs=[],
        model="m",
        work_dir=tmp_path,
        env={},
        claude_bin=FAKE,
    )
    assert status == "NEEDS-REVIEW"
    assert result["items"][0]["answer"] == "no"


def test_judge_failure_is_needs_review(tmp_path, monkeypatch):
    """JSON 解析失败返回 NEEDS-REVIEW，error 含 'parse'。"""
    rubric = tmp_path / "r.md"
    rubric.write_text("q")
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "不是 json")
    status, result = get_judge("single").judge(
        rubric=rubric,
        inputs=[],
        model="m",
        work_dir=tmp_path,
        env={},
        claude_bin=FAKE,
    )
    assert status == "NEEDS-REVIEW"
    assert "parse" in result["error"]


# ──────────────────────────────────────────────────
# JuryJudge stub
# ──────────────────────────────────────────────────

def test_get_judge_jury_stub(tmp_path):
    """get_judge('jury') 返回的 judge 调用时 raise NotImplementedError。"""
    jury = get_judge("jury")
    with pytest.raises(NotImplementedError, match="jury adapter"):
        jury.judge(
            rubric=tmp_path / "r.md",
            inputs=[],
            model="m",
            work_dir=tmp_path,
            env={},
            claude_bin=None,
        )


def test_get_judge_unknown_raises():
    """未知 judge 名报 KeyError。"""
    with pytest.raises(KeyError, match="unknown judge"):
        get_judge("nonexistent")


def test_distill_trace_keeps_answer_and_drops_thinking_noise():
    from harness.judge import distill_trace, _judge_prompt, INPUT_CHAR_LIMIT
    import json, pathlib, tempfile
    noise = "\n".join(json.dumps({"type": "system", "subtype": "thinking_tokens", "n": i}) for i in range(3000))
    hook = json.dumps({"type": "system", "subtype": "hook_response", "content": "SessionStart 注入：distill 会产出 template"})
    answer = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/SKILL.md"}},
        {"type": "text", "text": "distill 模式会产出 template 骨架与 pattern 判据，落盘前需人工确认。"}]}})
    result = json.dumps({"type": "result", "subtype": "success", "result": "最终回答：distill 已说明。"})
    raw = "\n".join([hook, noise, answer, result])
    assert len(raw) > INPUT_CHAR_LIMIT
    text = distill_trace(raw)
    assert "落盘前需人工确认" in text and "[tool_use Read]" in text and "最终回答" in text
    assert "thinking_tokens" not in text and "SessionStart 注入" not in text
    with tempfile.TemporaryDirectory() as d:
        tp = pathlib.Path(d) / "case.trace.jsonl"; tp.write_text(raw, encoding="utf-8")
        rb = pathlib.Path(d) / "rubric.md"; rb.write_text("是否提及 distill？", encoding="utf-8")
        prompt = _judge_prompt(rb, [str(tp)])
    assert "落盘前需人工确认" in prompt and "thinking_tokens" not in prompt
