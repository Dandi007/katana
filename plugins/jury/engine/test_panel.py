import json, os, sys, subprocess
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import panel

FAKE = str(Path(__file__).resolve().parent / "fake-claude-streamjson")

def _profile(tmp_path):
    # 造一个最小 profile：setter 只导出可观测 env，便于断言 base_url_used
    p = tmp_path / "profile.zsh"
    p.write_text(
        'set_claude_native_opus(){ unset ANTHROPIC_BASE_URL ANTHROPIC_MODEL; }\n'
        'set_claude_ccswitch_gpt(){ export ANTHROPIC_BASE_URL=http://127.0.0.1:15721; export ANTHROPIC_MODEL=gpt/gpt-5.5; }\n'
        'set_claude_ccswitch_ds(){ export ANTHROPIC_BASE_URL=http://127.0.0.1:15721; export ANTHROPIC_MODEL=lingzhi/deepseek-v4-pro; }\n'
        'set_claude_ccswitch_qwen(){ export ANTHROPIC_BASE_URL=http://127.0.0.1:15721; export ANTHROPIC_MODEL=lingzhi/qwen3.7-max; }\n'
    )
    return str(p)

def test_run_model_records_native_opus_empty_base_url(tmp_path):
    os.environ["JURY_CLAUDE_BIN"] = FAKE
    m = {"name": "opus", "setter": "set_claude_native_opus", "model": "opus"}
    r = panel.run_model(m, "review this", tmp_path, timeout=60, profile=_profile(tmp_path))
    assert r["base_url_used"] == ""          # 原生：无 override
    assert r["name"] == "opus"
    assert Path(r["trace_path"]).exists()
    assert r["vote"]["items"][0]["answer"] in ("yes", "no")

def test_run_model_records_ccs_base_url(tmp_path):
    os.environ["JURY_CLAUDE_BIN"] = FAKE
    m = {"name": "qwen", "setter": "set_claude_ccswitch_qwen", "model": ""}
    r = panel.run_model(m, "review this", tmp_path, timeout=60, profile=_profile(tmp_path))
    assert r["base_url_used"] == "http://127.0.0.1:15721"

def test_fanout_emits_three_artifacts_and_meta(tmp_path):
    os.environ["JURY_CLAUDE_BIN"] = FAKE
    out = tmp_path / "out"; out.mkdir()
    summary = panel.fanout("review this", out, panel.DEFAULT_ROSTER, 60, _profile(tmp_path))
    assert (out / "jury-report.md").exists()
    assert (out / "jury-verdict.json").exists()
    meta = json.loads((out / "panel-meta.json").read_text())
    assert len(meta) == 4
    assert {m["name"] for m in meta} == {"opus", "gpt", "deepseek", "qwen"}
    opus = next(m for m in meta if m["name"] == "opus")
    assert opus["base_url_used"] == ""
    assert summary["quorum"] == "full"

def test_fanout_partial_quorum_when_a_model_dies(tmp_path, monkeypatch):
    os.environ["JURY_CLAUDE_BIN"] = FAKE
    _real_run_model = panel.run_model  # 先保存，避免 monkeypatch 后递归
    def flaky(member, *a, **k):
        if member["name"] == "gpt":
            raise RuntimeError("boom")
        return _real_run_model(member, *a, **k)
    monkeypatch.setattr(panel, "run_model", flaky)
    out = tmp_path / "out"; out.mkdir()
    summary = panel.fanout("x", out, panel.DEFAULT_ROSTER, 60, _profile(tmp_path))
    assert summary["quorum"] == "partial"
    meta = json.loads((out / "panel-meta.json").read_text())
    gpt = next(m for m in meta if m["name"] == "gpt")
    assert gpt["exit"] != 0
    # I2: 幸存者真的成功了
    assert len(summary["ran"]) == 3
    assert "gpt" not in summary["ran"]


def test_parse_vote_takes_last_items_block():
    """verbose 模型先吐 example json 再吐真投票 → 应取末个含 items 的块。"""
    example_block = '```json\n{"note": "this is an example, not a vote"}\n```'
    real_vote_block = '```json\n{"items": [{"q": "1", "answer": "yes", "evidence": "e"}]}\n```'
    result_payload = (
        "Here is the format:\n" + example_block + "\n\nMy actual vote:\n" + real_vote_block + "\nprose"
    )
    stream_line = json.dumps({"type": "result", "result": result_payload})
    vote, prose = panel._parse_vote(stream_line)
    assert vote is not None, "应解析出投票"
    assert "items" in vote
    assert vote["items"][0]["answer"] == "yes"


def test_fanout_marks_unparseable_vote(tmp_path):
    FAKE_NO_VOTE = str(Path(__file__).resolve().parent / "fake-claude-noVote")
    os.environ["JURY_CLAUDE_BIN"] = FAKE_NO_VOTE
    roster = [{"name": "opus", "setter": "set_claude_native_opus", "model": "opus"}]
    out = tmp_path / "out"; out.mkdir()
    summary = panel.fanout("review this", out, roster, 60, _profile(tmp_path))
    # run_model 层：vote is None
    assert summary["members"][0]["vote"] is None
    # quorum partial（只有 1 个模型且没产出投票）
    assert summary["quorum"] == "partial"
    # panel-meta.json：vote_parsed=False 但 exit==0
    meta = json.loads((out / "panel-meta.json").read_text())
    opus_meta = next(m for m in meta if m["name"] == "opus")
    assert opus_meta["vote_parsed"] is False
    assert opus_meta["exit"] == 0


# ── 0.2 新增测试 ──────────────────────────────────────────────────────────────

def test_fanout_prepends_spec(tmp_path, monkeypatch):
    """spec 非空时，fanout 应在传给 run_model 的 prompt 前置评审目标头。
    用 monkeypatch 捕获 run_model 实际收到的 prompt 参数，验证前缀与 spec 内容均存在。
    """
    captured_prompts = []

    def fake_run_model(member, prompt, *args, **kwargs):
        captured_prompts.append(prompt)
        # 返回最小合法结构，让 fanout 能完成 tally/写文件
        return {
            "name": member["name"], "setter": member["setter"],
            "base_url_used": "", "model_string": "",
            "exit": 0, "trace_path": "",
            "vote": {"items": [{"q": "q1", "answer": "yes", "evidence": "e"}]},
            "prose": "ok",
        }

    monkeypatch.setattr(panel, "run_model", fake_run_model)
    out = tmp_path / "out"; out.mkdir()
    spec_text = "接口必须返回 200 且 body 含 status 字段"
    roster = [{"name": "opus", "setter": "set_claude_native_opus", "model": "opus"}]
    panel.fanout("请评审以下 diff", out, roster, 60, _profile(tmp_path),
                 spec=spec_text)

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert prompt.startswith("## 评审目标（spec）"), "prompt 应以 spec 头开头"
    assert spec_text in prompt, "prompt 应包含 spec 原文"
    assert "请评审以下 diff" in prompt, "原始 prompt 内容不应丢失"


def test_run_model_uses_target_cwd(tmp_path, monkeypatch):
    """target_dir 给定时，subprocess.run 应以 target_dir 作为 cwd。
    用 monkeypatch 捕获 subprocess.run 的 cwd 关键字参数验证。
    """
    captured_kwargs = {}

    def fake_subprocess_run(cmd, **kwargs):
        captured_kwargs.update(kwargs)
        # 返回最小 CompletedProcess，让 run_model 能解析后续逻辑
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout='{"type":"result","result":"```json\\n{\\"items\\":[{\\"q\\":\\"q1\\",\\"answer\\":\\"yes\\",\\"evidence\\":\\"e\\"}]}\\n```"}\n',
            stderr="__JURY_ENV__ base= model=",
        )

    monkeypatch.setattr(panel.subprocess, "run", fake_subprocess_run)
    out = tmp_path / "out"; out.mkdir()
    target = str(tmp_path / "repo")
    Path(target).mkdir()

    member = {"name": "opus", "setter": "set_claude_native_opus", "model": "opus"}
    panel.run_model(member, "review this", out, timeout=60,
                    profile=_profile(tmp_path), target_dir=target)

    assert captured_kwargs.get("cwd") == target, (
        f"subprocess.run 应以 target_dir={target!r} 作为 cwd，实际 cwd={captured_kwargs.get('cwd')!r}"
    )
