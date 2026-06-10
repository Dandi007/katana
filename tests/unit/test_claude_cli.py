import os
from pathlib import Path
import pytest
from harness.claude_cli import run_claude, ClaudeTimeout, run_claude_session

SHIM = str(Path(__file__).parent / "fake-claude")

def test_prompt_via_stdin_and_log(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "hello [[页]]")
    r = run_claude(prompt="做点事", cwd=tmp_path, log_path=tmp_path / "case.log",
                   model="lingzhi/claude-opus-4-8", permission_mode="acceptEdits",
                   allowed_tools=["Read"], timeout=30,
                   env={"CLAUDE_CONFIG_DIR": str(tmp_path)}, claude_bin=SHIM)
    assert r.exit_code == 0 and "[[页]]" in r.stdout
    assert (tmp_path / "case.log").read_text(encoding="utf-8") == r.stdout

def test_writes_land_in_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_WRITE", "out/note.md:body")
    run_claude(prompt="p", cwd=tmp_path, log_path=tmp_path / "l",
               model="m", permission_mode="acceptEdits", allowed_tools=[],
               timeout=30, env={}, claude_bin=SHIM)
    assert (tmp_path / "out/note.md").read_text() == "body"

def test_timeout_kills_and_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
    with pytest.raises(ClaudeTimeout):
        run_claude(prompt="p", cwd=tmp_path, log_path=tmp_path / "l",
                   model="m", permission_mode="acceptEdits", allowed_tools=[],
                   timeout=1, env={}, claude_bin=SHIM)


def test_session_multi_turn_accumulates(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "ok")
    monkeypatch.setenv("FAKE_CLAUDE_APPEND", str(tmp_path / "turns.log"))
    r = run_claude_session(
        turns=["a", "b", "c"], cwd=tmp_path, log_path=tmp_path / "case.log",
        model="m", permission_mode="acceptEdits", allowed_tools=[],
        timeout=30, env={}, claude_bin=SHIM)
    assert r.exit_code == 0
    assert (tmp_path / "turns.log").read_text().splitlines() == ["a", "b", "c"]
    assert r.stdout.count("resumed:s1") == 2   # 仅 turn2/turn3 带 --resume
    assert (tmp_path / "case.log").read_text() == r.stdout


def test_session_timeout_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP", "5")
    with pytest.raises(ClaudeTimeout):
        run_claude_session(
            turns=["x", "y"], cwd=tmp_path, log_path=tmp_path / "l",
            model="m", permission_mode="acceptEdits", allowed_tools=[],
            timeout=1, env={}, claude_bin=SHIM)


def test_session_raises_when_no_session_id_multi_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "ok")
    monkeypatch.setenv("FAKE_CLAUDE_BADJSON", "1")  # turn1 输出非 JSON → 拿不到 session_id
    with pytest.raises(RuntimeError, match="session_id"):
        run_claude_session(
            turns=["a", "b"], cwd=tmp_path, log_path=tmp_path / "l",
            model="m", permission_mode="acceptEdits", allowed_tools=[],
            timeout=30, env={}, claude_bin=SHIM)

def test_session_single_turn_list_tolerates_no_session_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_STDOUT", "ok")
    monkeypatch.setenv("FAKE_CLAUDE_BADJSON", "1")
    r = run_claude_session(
        turns=["only"], cwd=tmp_path, log_path=tmp_path / "l",
        model="m", permission_mode="acceptEdits", allowed_tools=[],
        timeout=30, env={}, claude_bin=SHIM)
    assert r.exit_code == 0  # 单轮不需要 resume，非 JSON 也不报错
