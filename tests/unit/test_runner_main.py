"""test_runner_main.py — v2 runner CLI 集成测试。

使用新三轴 schema（process/filesystem）契约 + fake-claude，
验证 runner.py main() 的 --all / --validate-only / --case / _resolve_verdict_inputs。
"""
import json, sys, textwrap
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[2]
SHIM = str(Path(__file__).parent / "fake-claude")


def make_mini_repo(tmp_path):
    """最小 katana 形状：1 plugin、1 skill、1 contract（v2 三轴 schema）、fixtures。

    fake-claude 会吐 Skill trace（input.skill="fake-skill"）并写 out.md（FAKE_CLAUDE_WRITE）。
    契约用 process+filesystem 两轴硬断言确保确定性（满足 schema 不变量）。
    """
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"name": "katana", "plugins": [
            {"name": "demo", "source": "./plugins/demo"}
        ]}))

    sd = tmp_path / "plugins/demo/skills/hello"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\nname: hello\ndescription: d\n---\nbody")

    cd = tmp_path / "plugins/demo/tests/contracts"
    cd.mkdir(parents=True)
    # v2 三轴 schema：process 验 skill_loaded，filesystem 验 created
    (cd / "hello.contract.yaml").write_text(textwrap.dedent("""\
        skill: demo:hello
        trigger:
          prompt: "打个招呼"
          model: lingzhi/claude-opus-4-8
        expect:
          process:
            - skill_loaded: fake-skill
          filesystem:
            - created: out.md
    """))

    (tmp_path / "tests/fixtures/kb").mkdir(parents=True)
    (tmp_path / "tests/fixtures/kb/seed.md").write_text("s")
    (tmp_path / "tests/fixtures/claude-config").mkdir()
    (tmp_path / "tests/reports").mkdir()
    (tmp_path / "tests/judge").mkdir()

    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path)
    return tmp_path


def test_sweep_all_with_fake_claude(tmp_path, monkeypatch):
    """--all 跑三轴契约：fake-claude 写 out.md + 吐 fake-skill trace → PASS。"""
    repo = make_mini_repo(tmp_path)
    r = subprocess.run(
        [sys.executable, str(REPO / "tests/runner.py"), "--all", "--jobs", "2",
         "--repo", str(repo), "--no-ccs-check", "--skip-judge"],
        env={
            "PATH": "/usr/bin:/bin",
            "CLAUDE_BIN": SHIM,
            # fake-claude 在 cwd 写 out.md（process 已由 stream-json Skill trace 满足）
            "FAKE_CLAUDE_WRITE": "out.md:hello",
            "PYTHONPATH": str(REPO / "tests"),
        },
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


def test_resolve_verdict_inputs_case_trace():
    """_resolve_verdict_inputs 支持 {case_trace} 和 {cwd} 占位符。"""
    import runner
    paths = runner._resolve_verdict_inputs(
        ["{case_trace}", "{cwd}/r.md"],
        Path("/x"), "kb",
    )
    assert str(paths[0]) == "/x/case.trace.jsonl"
    assert str(paths[1]) == "/x/kb/r.md"


def test_resolve_verdict_inputs_created(tmp_path):
    """_resolve_verdict_inputs 的 created 占位符展开 delta.created。"""
    import runner
    delta_info = {"created": {"a.md", "b.md"}, "modified": set(), "deleted": set()}
    paths = runner._resolve_verdict_inputs(["created"], tmp_path, "kb", delta_info)
    names = {p.name for p in paths}
    assert names == {"a.md", "b.md"}


def test_case_filter_no_match_exits_nonzero(tmp_path):
    repo = make_mini_repo(tmp_path)
    r = subprocess.run(
        [sys.executable, str(REPO / "tests/runner.py"), "--case", "typo:nope",
         "--repo", str(repo), "--no-ccs-check", "--skip-judge"],
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO / "tests")},
        capture_output=True, text=True)
    assert r.returncode != 0 and "matched no contracts" in (r.stdout + r.stderr)
