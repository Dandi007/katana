import json, os, sys
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
