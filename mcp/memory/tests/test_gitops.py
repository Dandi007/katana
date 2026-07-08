import os
import subprocess

from katana_memory_mcp import gitops


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    return str(tmp_path)


def test_commit_creates_git_commit(tmp_path):
    root = _init_repo(tmp_path)
    p = os.path.join(root, "uther", "a.md")
    os.makedirs(os.path.dirname(p))
    open(p, "w").write("x")
    r = gitops.commit(root, "chore(memory): [uther] create m-000001", [p])
    assert r["committed"] is True
    log = _git(root, "log", "--oneline").stdout
    assert "create m-000001" in log


def test_commit_deleted_path(tmp_path):
    root = _init_repo(tmp_path)
    p = os.path.join(root, "a.md")
    open(p, "w").write("x")
    gitops.commit(root, "add", [p])
    os.remove(p)
    r = gitops.commit(root, "chore(memory): delete", [p])
    assert r["committed"] is True


def test_commit_degrades_outside_git_repo(tmp_path):
    p = os.path.join(str(tmp_path), "a.md")
    open(p, "w").write("x")
    r = gitops.commit(str(tmp_path), "msg", [p])
    assert r["committed"] is False and r["detail"]


def test_commit_noop_when_nothing_changed(tmp_path):
    root = _init_repo(tmp_path)
    p = os.path.join(root, "a.md")
    open(p, "w").write("x")
    gitops.commit(root, "add", [p])
    r = gitops.commit(root, "again", [p])
    assert r["committed"] is False
