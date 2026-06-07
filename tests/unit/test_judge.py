import json
from pathlib import Path
import pytest
from harness.judge import run_case_verdict, parse_verdict_json

SHIM = str(Path(__file__).parent / "fake-claude")


def test_parse_fenced_json():
    """从混合文本中提取 fenced json。"""
    txt = '前言\n```json\n{"items": [{"q": "x", "answer": "yes", "evidence": "e"}]}\n```\n尾巴'
    v = parse_verdict_json(txt)
    assert v["items"][0]["answer"] == "yes"


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
    status, result = run_case_verdict(
        rubric=rubric,
        inputs=[artifact],
        model="m",
        work_dir=tmp_path,
        claude_bin=SHIM,
        base_env={},
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
    status, result = run_case_verdict(
        rubric=rubric, inputs=[], model="m", work_dir=tmp_path, claude_bin=SHIM, base_env={}
    )
    assert status == "NEEDS-REVIEW"
    assert result["items"][0]["answer"] == "no"


def test_judge_failure_is_needs_review(tmp_path, monkeypatch):
    """JSON 解析失败返回 NEEDS-REVIEW。"""
    rubric = tmp_path / "r.md"
    rubric.write_text("q")
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "不是 json")
    status, result = run_case_verdict(
        rubric=rubric, inputs=[], model="m", work_dir=tmp_path, claude_bin=SHIM, base_env={}
    )
    assert status == "NEEDS-REVIEW"
    assert "parse" in result["error"]
