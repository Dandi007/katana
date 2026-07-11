"""Async remote push against a real local bare remote (operator P1 #8, §6.7).

Exercises the fast-forward-only push worker end to end: pending → fast-forward
sync → idempotent already-synced, coalescing multiple pending commits, and a
diverged remote failing closed with REMOTE_DIVERGED (never an auto merge/force).
"""
import subprocess

import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import KernelError


def _git(root, *a, check=True):
    return subprocess.run(["git", "-C", root, *a], check=check,
                          capture_output=True, text=True)


def _engine_with_remote(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    eng = kernel.TransactionEngine(str(repo), domain="test")
    eng.repo.ensure_repo()
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    _git(str(repo), "remote", "add", "origin", str(bare))
    return eng, str(repo), str(bare)


def _create(eng, rid, path, body):
    b = MutationBatch(domain="test")
    b.add(Change(op=Op.CREATE, resource_id=rid, after_path=path,
                 after_content=body))
    return eng.commit(b, message=f"create {path}")


def test_pending_then_fast_forward_sync(tmp_path):
    eng, repo, bare = _engine_with_remote(tmp_path)
    _create(eng, "t-1", "a.md", b"a\n")
    assert eng.status()["push"]["pending_commits"] == 1
    out = eng.push_remote("origin")
    assert out["status"] == "synced"
    assert eng.status()["push"]["pending_commits"] == 0
    # Idempotent: a second push is a no-op already-synced.
    out2 = eng.push_remote("origin")
    assert out2["status"] == "already_synced"


def test_push_coalesces_multiple_pending(tmp_path):
    eng, repo, bare = _engine_with_remote(tmp_path)
    _create(eng, "t-1", "a.md", b"a\n")
    _create(eng, "t-2", "b.md", b"b\n")
    _create(eng, "t-3", "c.md", b"c\n")
    assert eng.status()["push"]["pending_commits"] == 3
    eng.push_remote("origin")
    assert eng.status()["push"]["pending_commits"] == 0


def test_diverged_remote_fails_closed(tmp_path):
    eng, repo, bare = _engine_with_remote(tmp_path)
    _create(eng, "t-1", "a.md", b"a\n")
    eng.push_remote("origin")

    # Make the remote diverge: commit directly into the bare remote via a clone.
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", bare, str(clone)], check=True)
    _git(str(clone), "config", "user.email", "x@x")
    _git(str(clone), "config", "user.name", "x")
    (clone / "rogue.md").write_text("rogue\n", encoding="utf-8")
    _git(str(clone), "add", "-A")
    _git(str(clone), "commit", "-qm", "divergent remote commit")
    _git(str(clone), "push", "-q", "origin", "HEAD")

    # Local also advances → histories diverge.
    _create(eng, "t-2", "b.md", b"b\n")
    with pytest.raises(KernelError) as ei:
        eng.push_remote("origin")
    assert ei.value.code == "REMOTE_DIVERGED"
    # Local canonical head remains authoritative; nothing was force-pushed.
    assert eng.status()["push"]["pending_commits"] >= 1
