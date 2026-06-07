import json, sys, textwrap
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
SHIM = str(Path(__file__).parent / "fake-claude")


def make_mini_repo(tmp_path):
    """最小 katana 形状：1 plugin、1 skill、1 contract、fixtures。"""
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "katana", "plugins": [{"name": "demo", "source": "./plugins/demo"}]}))
    sd = tmp_path / "plugins/demo/skills/hello"; sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\nname: hello\ndescription: d\n---\nbody")
    cd = tmp_path / "plugins/demo/tests/contracts"; cd.mkdir(parents=True)
    (cd / "hello.contract.yaml").write_text(textwrap.dedent("""\
        skill: demo:hello
        input: {prompt: "打个招呼"}
        assert: [{stdout_grep: "fake output"}]
    """))
    (tmp_path / "tests/fixtures/kb").mkdir(parents=True)
    (tmp_path / "tests/fixtures/kb/seed.md").write_text("s")
    (tmp_path / "tests/fixtures/claude-config").mkdir()
    (tmp_path / "tests/reports").mkdir()
    (tmp_path / "tests/judge").mkdir()
    (tmp_path / "tests/judge/overall-rubric.md").write_text("整体 ok 吗？输出 fenced json")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path)
    return tmp_path


def test_sweep_all_with_fake_claude(tmp_path, monkeypatch):
    repo = make_mini_repo(tmp_path)
    r = subprocess.run(
        [sys.executable, str(REPO / "tests/runner.py"), "--all", "--jobs", "2",
         "--repo", str(repo), "--no-ccs-check", "--skip-judge"],
        env={"PATH": "/usr/bin:/bin", "CLAUDE_BIN": SHIM,
             "FAKE_CLAUDE_STDOUT": "fake output",
             "PYTHONPATH": str(REPO / "tests")},
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    reports = list((repo / "tests/reports").glob("*.md"))
    assert reports and "PASS 1 / FAIL 0" in reports[0].read_text()


def test_validate_only(tmp_path):
    repo = make_mini_repo(tmp_path)
    r = subprocess.run(
        [sys.executable, str(REPO / "tests/runner.py"), "--validate-only",
         "--repo", str(repo)],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "tests")},
        capture_output=True, text=True)
    assert r.returncode == 0 and "1 contracts valid" in r.stdout


def test_case_filter_no_match_exits_nonzero(tmp_path):
    repo = make_mini_repo(tmp_path)
    r = subprocess.run(
        [sys.executable, str(REPO / "tests/runner.py"), "--case", "typo:nope",
         "--repo", str(repo), "--no-ccs-check", "--skip-judge"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "tests")},
        capture_output=True, text=True)
    assert r.returncode != 0 and "matched no contracts" in (r.stdout + r.stderr)
