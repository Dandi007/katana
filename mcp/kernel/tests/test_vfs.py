"""Unit tests for GovernedVFS."""

import os
import tempfile

import pytest

from katana_kernel.vfs import GovernedVFS, VFSError


@pytest.fixture
def vfs_root():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "sub"), exist_ok=True)
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_vfs_write_and_read(vfs_root):
    vfs = GovernedVFS(vfs_root)
    vfs.write("test.md", "hello")
    assert vfs.read_text("test.md") == "hello"


def test_vfs_rejects_absolute_path(vfs_root):
    vfs = GovernedVFS(vfs_root)
    with pytest.raises(VFSError, match="absolute"):
        vfs.write("/etc/passwd", "bad")


def test_vfs_rejects_path_traversal(vfs_root):
    vfs = GovernedVFS(vfs_root)
    with pytest.raises(VFSError, match="traversal"):
        vfs.write("sub/../../escape", "bad")


def test_vfs_rejects_symlink_escape(vfs_root):
    vfs = GovernedVFS(vfs_root)
    os.symlink("/etc", os.path.join(vfs_root, "link"))
    with pytest.raises(VFSError, match="symlink"):
        vfs.write("link/passwd", "bad")


def test_vfs_rejects_symlink_mid_path(vfs_root):
    vfs = GovernedVFS(vfs_root)
    os.symlink("/etc", os.path.join(vfs_root, "sub", "link"))
    with pytest.raises(VFSError, match="symlink"):
        vfs.write("sub/link/passwd", "bad")


def test_vfs_rejects_resolve_escape(vfs_root):
    vfs = GovernedVFS(vfs_root)
    escape_dir = os.path.join(vfs_root, "deep")
    os.makedirs(escape_dir, exist_ok=True)
    os.symlink(vfs_root, os.path.join(escape_dir, "loop"))
    with pytest.raises(VFSError):
        vfs.write("deep/loop/../outside", "bad")


def test_vfs_delete(vfs_root):
    vfs = GovernedVFS(vfs_root)
    vfs.write("del.md", "x")
    assert vfs.exists("del.md")
    vfs.delete("del.md")
    assert not vfs.exists("del.md")


def test_vfs_ls(vfs_root):
    vfs = GovernedVFS(vfs_root)
    vfs.write("a.md", "a")
    vfs.write("b.md", "b")
    vfs.write(".hidden", "h")
    entries = vfs.ls("*.md")
    assert "a.md" in entries
    assert "b.md" in entries
    assert ".hidden" not in entries


def test_vfs_rename(vfs_root):
    vfs = GovernedVFS(vfs_root)
    vfs.write("old.md", "x")
    vfs.rename("old.md", "new.md")
    assert not vfs.exists("old.md")
    assert vfs.exists("new.md")
    assert vfs.read_text("new.md") == "x"


def test_vfs_rename_target_exists(vfs_root):
    vfs = GovernedVFS(vfs_root)
    vfs.write("a.md", "a")
    vfs.write("b.md", "b")
    with pytest.raises(VFSError, match="already exists"):
        vfs.rename("a.md", "b.md")


def test_vfs_write_mkdirs(vfs_root):
    vfs = GovernedVFS(vfs_root)
    vfs.write("deep/nested/file.md", "deep")
    assert vfs.read_text("deep/nested/file.md") == "deep"


def test_vfs_root_is_not_dir():
    with pytest.raises(VFSError, match="not a directory"):
        GovernedVFS("/nonexistent/path/12345")


def test_vfs_raw_escape_unreachable(vfs_root):
    vfs = GovernedVFS(vfs_root)
    with pytest.raises(VFSError):
        vfs.write("/tmp/escaped.txt", "no")