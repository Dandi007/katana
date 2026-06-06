import os
from pathlib import Path
import pytest
from harness.claude_cli import run_claude, ClaudeTimeout

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
