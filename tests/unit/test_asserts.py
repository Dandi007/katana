import json
import pytest
from pathlib import Path
from harness.asserts import run_asserts, Ctx


def ctx(tmp_path, stdout=""):
    (tmp_path / "kb").mkdir(exist_ok=True)
    log = tmp_path / "case.log"
    log.write_text(stdout, encoding="utf-8")
    return Ctx(
        cwd=tmp_path / "kb",
        stdout=stdout,
        case_log=log,
        contract_dir=tmp_path,
    )


def test_file_exists_glob(tmp_path):
    c = ctx(tmp_path)
    (c.cwd / "小红书-盒马").mkdir()
    (c.cwd / "小红书-盒马" / "index.md").write_text("x")
    r = run_asserts([{"file_exists": "{cwd}/小红书-*/index.md"}], c)
    assert r[0].ok


def test_file_absent(tmp_path):
    c = ctx(tmp_path)
    assert run_asserts([{"file_absent": "{cwd}/raw/*.md"}], c)[0].ok
    (c.cwd / "raw").mkdir()
    (c.cwd / "raw" / "a.md").write_text("x")
    assert not run_asserts([{"file_absent": "{cwd}/raw/*.md"}], c)[0].ok


def test_file_grep_and_size(tmp_path):
    c = ctx(tmp_path)
    n = c.cwd / "note.md"
    n.write_text("---\nauthor: momo\n---\n" + "x" * 600)
    assert run_asserts([{"file_grep": {"path": "{cwd}/note.md", "pattern": "^author:"}}], c)[0].ok
    assert run_asserts([{"size_min": {"path": "{cwd}/note.md", "bytes": 500}}], c)[0].ok
    assert not run_asserts([{"size_min": {"path": "{cwd}/note.md", "bytes": 99999}}], c)[0].ok


def test_stdout_grep(tmp_path):
    c = ctx(tmp_path, stdout="答案见 [[手冲咖啡]] 页")
    assert run_asserts([{"stdout_grep": "\\[\\[.*\\]\\]"}], c)[0].ok


def test_json_path(tmp_path):
    c = ctx(tmp_path)
    (c.cwd / "r.json").write_text(json.dumps({"a": {"b": "ok"}}))
    assert run_asserts([{"json_path": {"file": "{cwd}/r.json", "path": "$.a.b", "equals": "ok"}}], c)[0].ok


def test_script_escape_hatch(tmp_path):
    c = ctx(tmp_path)
    s = tmp_path / "v.sh"
    s.write_text("#!/bin/bash\n[ -d \"$KB_DIR\" ]\n")
    s.chmod(0o755)
    assert run_asserts([{"script": "v.sh"}], c)[0].ok


def test_failure_carries_detail(tmp_path):
    c = ctx(tmp_path)
    r = run_asserts([{"file_exists": "{cwd}/nope.md"}], c)[0]
    assert not r.ok and "nope.md" in r.detail


def test_script_failure_and_escape(tmp_path):
    c = ctx(tmp_path)
    bad = tmp_path / "bad.sh"
    bad.write_text("#!/bin/bash\necho boom >&2\nexit 3\n")
    r = run_asserts([{"script": "bad.sh"}], c)[0]
    assert not r.ok and "boom" in r.detail
    r2 = run_asserts([{"script": "../../etc/evil.sh"}], c)[0]
    assert not r2.ok and "escapes" in r2.detail


def test_script_env_isolated(tmp_path):
    c = ctx(tmp_path)
    s = tmp_path / "env.sh"
    s.write_text("#!/bin/bash\n[ -z \"${HOME:-}\" ]\n")
    assert run_asserts([{"script": "env.sh"}], c)[0].ok
