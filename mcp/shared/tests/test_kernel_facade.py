"""Governed Full VFS façade tests (design §5.2, INV-5, INV-10).

Anchors the fs_* operation set, identity semantics (rename keeps id, copy mints
new id, delete tombstones), path confinement, reserved-namespace hiding and
single-repo batch atomicity.
"""
import pytest

from katana_kb_mcp_shared import kernel
from katana_kb_mcp_shared.kernel.catalog import Catalog
from katana_kb_mcp_shared.kernel.errors import KernelError
from katana_kb_mcp_shared.kernel.facade import GovernedVFS


class _Policy:
    domain = "test"
    id_prefix = "t-"
    policy_version = 1

    def __init__(self):
        self.validated = []

    def validate(self, batch):
        self.validated.append(batch)


@pytest.fixture
def vfs(tmp_path):
    eng = kernel.TransactionEngine(str(tmp_path), domain="test")
    eng.repo.ensure_repo()
    return GovernedVFS(eng, Catalog(str(tmp_path), id_prefix="t-"), _Policy())


def test_create_mints_id_and_commits(vfs):
    r = vfs.fs_create(virtual_path="notes/a.md", content="hello\n")
    assert r["resource_id"].startswith("t-")
    assert r["commit_sha"]
    assert r["virtual_path"] == "notes/a.md"


def test_create_duplicate_path_rejected(vfs):
    vfs.fs_create(virtual_path="a.md", content="x")
    with pytest.raises(KernelError):
        vfs.fs_create(virtual_path="a.md", content="y")


def test_write_does_not_implicitly_create(vfs):
    with pytest.raises(KernelError) as ei:
        vfs.fs_write(virtual_path="ghost.md", content="x")
    assert ei.value.code == "NOT_FOUND"


def test_read_returns_uniform_descriptor(vfs):
    r = vfs.fs_create(virtual_path="a.md", content="line1\nline2\n")
    rd = vfs.fs_read(resource_id=r["resource_id"])
    for k in ("resource_id", "virtual_path", "content_hash",
              "resource_revision", "content_revision", "total_lines"):
        assert k in rd
    assert rd["total_lines"] == 3


def test_read_offset_limit(vfs):
    vfs.fs_create(virtual_path="a.md", content="l1\nl2\nl3\n")
    rd = vfs.fs_read(virtual_path="a.md", offset=1, limit=1)
    assert rd["content"].splitlines() == ["1\tl1"]


def test_edit_exact_match(vfs):
    r = vfs.fs_create(virtual_path="a.md", content="hello world\n")
    vfs.fs_edit(resource_id=r["resource_id"], old_string="hello", new_string="hi")
    rd = vfs.fs_read(resource_id=r["resource_id"])
    assert "hi world" in rd["content"]


def test_edit_ambiguous_requires_replace_all(vfs):
    r = vfs.fs_create(virtual_path="a.md", content="x x\n")
    with pytest.raises(KernelError):
        vfs.fs_edit(resource_id=r["resource_id"], old_string="x", new_string="y")
    vfs.fs_edit(resource_id=r["resource_id"], old_string="x", new_string="y",
                replace_all=True)


def test_rename_keeps_id(vfs):
    r = vfs.fs_create(virtual_path="a.md", content="x")
    rid = r["resource_id"]
    r2 = vfs.fs_rename(resource_id=rid, new_path="sub/b.md")
    assert r2["resource_id"] == rid
    assert vfs.catalog.path_of(rid) == "sub/b.md"


def test_copy_mints_new_id(vfs):
    r = vfs.fs_create(virtual_path="a.md", content="x")
    r2 = vfs.fs_copy(resource_id=r["resource_id"], new_path="b.md")
    assert r2["resource_id"] != r["resource_id"]


def test_delete_tombstones_and_id_not_reused(vfs):
    r = vfs.fs_create(virtual_path="a.md", content="x")
    rid = r["resource_id"]
    vfs.fs_delete(resource_id=rid)
    assert vfs.catalog.is_tombstoned(rid)
    # mint again many times; tombstoned id must never come back
    for _ in range(50):
        assert vfs.catalog.mint("z.md") != rid
        vfs.catalog.tombstone(vfs.catalog.id_of("z.md"))


def test_ref_mismatch(vfs):
    a = vfs.fs_create(virtual_path="a.md", content="x")
    vfs.fs_create(virtual_path="b.md", content="y")
    with pytest.raises(KernelError) as ei:
        vfs.fs_read(resource_id=a["resource_id"], virtual_path="b.md")
    assert ei.value.code == "REF_MISMATCH"


def test_confinement_rejected_by_facade(vfs):
    with pytest.raises(KernelError):
        vfs.fs_create(virtual_path="../escape.md", content="x")


def test_reserved_namespace_hidden_from_list(vfs):
    vfs.fs_create(virtual_path="a.md", content="x")
    listing = [n["virtual_path"] for n in vfs.fs_list("")]
    assert ".kb" not in listing
    assert ".git" not in listing


def test_batch_is_single_commit(vfs):
    r = vfs.fs_batch([
        {"op": "create", "virtual_path": "x.md", "content": "X"},
        {"op": "create", "virtual_path": "y.md", "content": "Y"},
    ])
    assert r["commit_sha"]
    assert len(r["changes"]) == 2
    assert len(vfs.fs_glob("*.md")) == 2


def test_every_mutation_runs_policy_validate(vfs):
    vfs.fs_create(virtual_path="a.md", content="x")
    assert vfs.policy.validated, "fs_create must run policy.validate"
