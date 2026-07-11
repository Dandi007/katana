"""Path-confinement, symlink-escape and canonical-read anchors.

Covers the reviewer/operator P0 findings the previous attempt missed:
- ``fs_read``/discovery serve committed bytes from a pinned Git snapshot, never
  out-of-band working-tree mutations (reviewer #1);
- ``fs_list`` denies direct reserved-namespace enumeration (reviewer #2);
- ``fs_glob`` is confined to the canonical tree and cannot escape the repo or
  enumerate reserved internals (reviewer #3, operator P0 #1);
- ``fs_batch.from_path`` is confined so a rename/delete cannot target a host
  path outside the repo (operator P0 #1);
- discovery responses carry the uniform canonical node descriptor (reviewer #4).

Removing the product-side guards (e.g. reverting fs_list to ``paths.normalize``
or fs_glob to host ``glob.glob``) makes these tests fail.
"""
import os

import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.catalog import Catalog
from katana_kb_mcp_shared.kernel.errors import KernelError
from katana_kb_mcp_shared.kernel.facade import GovernedVFS


class _Policy:
    domain = "test"
    id_prefix = "t-"
    policy_version = 1

    def validate(self, batch):
        return None


@pytest.fixture
def vfs(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    eng.repo.ensure_repo()
    return GovernedVFS(eng, Catalog(str(tmp_path), id_prefix="t-"), _Policy())


# ── canonical read from pinned snapshot (reviewer #1) ─────────────────

def test_fs_read_returns_committed_bytes_not_worktree(vfs, tmp_path):
    r = vfs.fs_create(virtual_path="a.md", content="committed\n")
    # Tamper the tracked working-tree file out-of-band.
    (tmp_path / "a.md").write_text("TAMPERED\n", encoding="utf-8")
    rd = vfs.fs_read(resource_id=r["resource_id"])
    assert "committed" in rd["content"]
    assert "TAMPERED" not in rd["content"]
    # Snapshot commit is pinned to HEAD.
    assert rd["snapshot_commit"] == vfs.engine.repo.head()


def test_fs_stat_reports_committed_content_hash(vfs, tmp_path):
    r = vfs.fs_create(virtual_path="a.md", content="hello\n")
    committed = vfs.fs_stat(resource_id=r["resource_id"])["content_hash"]
    (tmp_path / "a.md").write_text("TAMPERED\n", encoding="utf-8")
    assert vfs.fs_stat(resource_id=r["resource_id"])["content_hash"] == committed


# ── fs_list denies reserved-namespace enumeration (reviewer #2) ───────

@pytest.mark.parametrize("reserved", [".kb", ".git", ".kb/nested", ".git/refs"])
def test_fs_list_rejects_reserved_namespace(vfs, reserved):
    vfs.fs_create(virtual_path="a.md", content="x")
    with pytest.raises(KernelError) as ei:
        vfs.fs_list(reserved)
    assert ei.value.code == "INVALID_PATH"


def test_fs_list_root_hides_reserved(vfs):
    vfs.fs_create(virtual_path="a.md", content="x")
    paths = {n["virtual_path"] for n in vfs.fs_list("")}
    assert ".kb" not in paths and ".git" not in paths


def test_fs_list_returns_uniform_descriptor(vfs):
    vfs.fs_create(virtual_path="a.md", content="hi\n")
    node = [n for n in vfs.fs_list("") if n["virtual_path"] == "a.md"][0]
    for k in ("resource_id", "virtual_path", "node_type", "size",
              "media_type", "content_hash", "resource_revision",
              "content_revision", "snapshot_commit"):
        assert k in node


# ── fs_glob is confined to the canonical tree (reviewer #3, P0 #1) ────

@pytest.mark.parametrize("bad", [
    "../*", "/etc/*", "..", "a/../../b", "*\\*", ".git/**", ".kb/**",
])
def test_fs_glob_rejects_escape_and_reserved(vfs, bad):
    vfs.fs_create(virtual_path="a.md", content="x")
    with pytest.raises(KernelError) as ei:
        vfs.fs_glob(bad)
    assert ei.value.code == "INVALID_PATH"


def test_fs_glob_matches_only_canonical_tree(vfs, tmp_path):
    vfs.fs_create(virtual_path="a.md", content="x")
    vfs.fs_create(virtual_path="sub/b.md", content="y")
    # An out-of-band file in the working tree must NOT appear (canonical only).
    (tmp_path / "rogue.md").write_text("z\n", encoding="utf-8")
    top = [n["virtual_path"] for n in vfs.fs_glob("*.md")]
    assert top == ["a.md"]
    deep = sorted(n["virtual_path"] for n in vfs.fs_glob("**/*.md"))
    assert "sub/b.md" in deep
    assert "rogue.md" not in deep


def test_fs_glob_returns_descriptors(vfs):
    vfs.fs_create(virtual_path="a.md", content="x")
    node = vfs.fs_glob("*.md")[0]
    assert node["resource_id"] and node["content_hash"].startswith("sha256:")


# ── fs_batch.from_path confinement (operator P0 #1) ───────────────────

def test_fs_batch_from_path_confined(vfs, tmp_path):
    r = vfs.fs_create(virtual_path="a.md", content="x")
    sentinel = tmp_path.parent / "host-sentinel.txt"
    sentinel.write_text("do-not-delete\n", encoding="utf-8")
    with pytest.raises(KernelError) as ei:
        vfs.fs_batch([
            {"op": "rename", "resource_id": r["resource_id"],
             "from_path": "../host-sentinel.txt", "virtual_path": "b.md"},
        ])
    assert ei.value.code == "INVALID_PATH"
    # The host sentinel is untouched.
    assert sentinel.read_text() == "do-not-delete\n"


def test_fs_batch_delete_from_path_confined(vfs, tmp_path):
    sentinel = tmp_path.parent / "host-del.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    with pytest.raises(KernelError) as ei:
        vfs.fs_batch([
            {"op": "delete", "resource_id": "t-x",
             "from_path": "../host-del.txt"},
        ])
    assert ei.value.code == "INVALID_PATH"
    assert sentinel.exists()
