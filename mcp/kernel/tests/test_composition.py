"""composition contract tests: kernel + memory end-to-end, per spec L25-33.

Each test exercises the full governed chain: kernel.mutate -> policy -> CAS ->
VFS -> ledger -> manifest -> git commit, and verifies invariants via git history,
working tree, and actual resource_id behaviour.
"""

import os
import subprocess
import tempfile

import pytest

from katana_kernel import (
    CASRejectionError,
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    ResourceIdLedger,
    TransactionManifest,
    head_sha,
    is_working_tree_clean,
)
from katana_memory_mcp.store import MemoryStore


def _make_git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    with open(os.path.join(d, ".gitkeep"), "w") as f:
        f.write("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, check=True, capture_output=True)
    tenant = "test-tenant"
    os.makedirs(os.path.join(d, tenant))
    return d, tenant


def _memory_policy():
    def _invariants(domain, op, args):
        if op in ("create", "update", "edit"):
            body = args.get("body")
            if body is not None:
                if "## Fact" not in body:
                    raise ValueError("body must contain '## Fact' section")
                if "## How to Verify" not in body:
                    raise ValueError("body must contain '## How to Verify' section")
        if op == "create" and not args.get("body"):
            raise ValueError("body is required for create")

    return DomainPolicy(
        domain="memory",
        allowed_ops={"create", "update", "delete", "edit", "list", "get", "read"},
        invariants=[_invariants],
    )


def _setup_kernel_and_store(repo_root):
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo_root)
    ledger = ResourceIdLedger(os.path.join(repo_root, ".katana", "tombstones.json"))
    manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
    policy = _memory_policy()
    kernel.bind("memory", policy, vfs, ledger, manifest, repo_root)
    store = MemoryStore(kernel)
    return kernel, store


# --- 1. CAS: stale expected_base_sha rejected (spec L27) ---

def test_composition_cas_rejects_stale_sha():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create_card(tenant, "card-a", "desc a",
                          "## Fact\nx\n\n## How to Verify\ny")
        with pytest.raises(CASRejectionError):
            store.create_card(tenant, "card-b", "desc b",
                              "## Fact\nx\n\n## How to Verify\ny",
                              expected_base_sha="a" * 40)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_cas_rejects_stale_sha_on_update():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-c", "desc c",
                                   "## Fact\nx\n\n## How to Verify\ny")
        cid = result["id"]
        with pytest.raises(CASRejectionError):
            store.update_card(tenant, cid, description="new desc",
                              expected_base_sha="a" * 40)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_cas_rejects_stale_sha_on_delete():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-d", "desc d",
                                   "## Fact\nx\n\n## How to Verify\ny")
        with pytest.raises(CASRejectionError):
            store.delete_card(tenant, result["id"], expected_base_sha="a" * 40)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 2. durable manifest in git history + working tree clean (spec L28) ---

def test_composition_manifest_in_git_history():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-e", "desc e",
                                   "## Fact\nx\n\n## How to Verify\ny")
        assert result["git"]["committed"] is True
        manifest_id = result["manifest"]["manifest_id"]
        git_log = subprocess.run(
            ["git", "-C", repo_root, "log", "--oneline", "-n", "5"],
            capture_output=True, text=True,
        )
        assert "create card-e" in git_log.stdout
        manifest_committed = False
        for line in subprocess.run(
            ["git", "-C", repo_root, "ls-tree", "-r", "HEAD", "--name-only"],
            capture_output=True, text=True,
        ).stdout.splitlines():
            if line.startswith(".katana/manifests/") and manifest_id in line:
                show = subprocess.run(
                    ["git", "-C", repo_root, "show", f"HEAD:{line}"],
                    capture_output=True, text=True,
                )
                if manifest_id in show.stdout:
                    manifest_committed = True
                    break
        assert manifest_committed, "manifest not found in git history"
        assert is_working_tree_clean(repo_root), "working tree is not clean"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_manifest_git_field_populated():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-git", "desc git",
                                   "## Fact\nx\n\n## How to Verify\ny")
        manifest_id = result["manifest"]["manifest_id"]
        import json
        found = False
        for line in subprocess.run(
            ["git", "-C", repo_root, "ls-tree", "-r", "HEAD", "--name-only"],
            capture_output=True, text=True,
        ).stdout.splitlines():
            if line.startswith(".katana/manifests/") and manifest_id in line:
                show = subprocess.run(
                    ["git", "-C", repo_root, "show", f"HEAD:{line}"],
                    capture_output=True, text=True,
                )
                manifest_data = json.loads(show.stdout)
                git_field = manifest_data.get("git", {})
                assert isinstance(git_field, dict), "git field must be a dict"
                assert git_field, f"git field must be non-empty, got {git_field!r}"
                assert git_field.get("committed") is True, "git.committed must be True"
                found = True
                break
        assert found, "manifest not found in committed git history"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_working_tree_clean_after_delete():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-f", "desc f",
                                   "## Fact\nx\n\n## How to Verify\ny")
        store.delete_card(tenant, result["id"])
        assert is_working_tree_clean(repo_root), "working tree not clean after delete"
        git_log = subprocess.run(
            ["git", "-C", repo_root, "log", "--oneline", "-n", "5"],
            capture_output=True, text=True,
        )
        assert "delete" in git_log.stdout
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 3. resource_id tombstone prevents reuse (spec L29) ---

def test_composition_resource_id_not_reused_after_delete():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-g", "desc g",
                                   "## Fact\nx\n\n## How to Verify\ny")
        deleted_id = result["id"]
        store.delete_card(tenant, deleted_id)
        new_result = store.create_card(tenant, "card-h", "desc h",
                                       "## Fact\nx\n\n## How to Verify\ny")
        assert new_result["id"] != deleted_id, "tombstoned id was reused"
        assert kernel.get_binding("memory").ledger.is_tombstoned(deleted_id)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_resource_id_not_reused_force_collision():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-i", "desc i",
                                   "## Fact\nx\n\n## How to Verify\ny")
        deleted_id = result["id"]
        store.delete_card(tenant, deleted_id)
        binding = kernel.get_binding("memory")
        for _ in range(50):
            new_id = binding.ledger.gen_id({deleted_id})
            assert new_id != deleted_id, f"tombstoned id {deleted_id} reused as {new_id}"
        new_result = store.create_card(tenant, "card-j", "desc j",
                                       "## Fact\nx\n\n## How to Verify\ny")
        assert new_result["id"] != deleted_id
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 4. governed VFS: reject path traversal / cross-domain write (spec L30) ---

def test_composition_vfs_rejects_path_traversal():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-k", "desc k",
                                   "## Fact\nx\n\n## How to Verify\ny")
        cid = result["id"]
        with pytest.raises(Exception):
            store.edit_card(tenant, cid, "card-k", "../escape")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_vfs_no_raw_escape():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("memory").vfs
        with pytest.raises(Exception):
            vfs.write("/tmp/escaped.txt", "should not work")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 5. memory domain invariant: create missing ## sections rejected (spec L31) ---

def test_composition_create_rejects_missing_fact():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        with pytest.raises(ValueError, match="## Fact"):
            store.create_card(tenant, "card-l", "desc l",
                              "## How to Verify\nrun but no fact")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_create_rejects_missing_how_to_verify():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        with pytest.raises(ValueError, match="## How to Verify"):
            store.create_card(tenant, "card-m", "desc m",
                              "## Fact\nfact but no verify")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_update_rejects_bad_body():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create_card(tenant, "card-n", "desc n",
                                   "## Fact\nx\n\n## How to Verify\ny")
        with pytest.raises(ValueError, match="## Fact"):
            store.update_card(tenant, result["id"], body="bad body no fact")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 6. single authoritative writer: second domain binding rejected (spec L32) ---

def test_composition_duplicate_domain_name_rejected():
    repo_root, tenant = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        policy = _memory_policy()
        ledger = ResourceIdLedger(os.path.join(repo_root, ".katana", "tombstones.json"))
        manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        kernel.bind("memory", policy, vfs, ledger, manifest, repo_root)
        with pytest.raises(ValueError, match="already bound"):
            kernel.bind("memory", policy, vfs, ledger, manifest, repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_different_domain_same_repo_rejected():
    repo_root, tenant = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        policy = _memory_policy()
        ledger = ResourceIdLedger(os.path.join(repo_root, ".katana", "tombstones.json"))
        manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        kernel.bind("memory", policy, vfs, ledger, manifest, repo_root)
        with pytest.raises(ValueError, match="already bound"):
            kernel.bind("wiki", policy, vfs, ledger, manifest, repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 7. working tree clean after CAS race / failure (spec L12, R4) ---

def test_composition_working_tree_clean_after_cas_rejection():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        with pytest.raises(CASRejectionError):
            store.create_card(tenant, "card-cas", "desc cas",
                              "## Fact\nx\n\n## How to Verify\ny",
                              expected_base_sha="a" * 40)
        assert is_working_tree_clean(repo_root), \
            "working tree must be clean after CAS rejection"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- Additional: data_root default is not /data/memory (spec L13) ---

def test_composition_data_root_not_default_slash_data_memory():
    from katana_memory_mcp.server import _resolve_data_root
    old = os.environ.pop("KATANA_MEMORY_DIR", None)
    try:
        root = _resolve_data_root()
        assert root != "/data/memory", "default data root must not be /data/memory"
    finally:
        if old is not None:
            os.environ["KATANA_MEMORY_DIR"] = old


# --- Regression: sequential CAS uses returned SHA (feedback PRIMARY) ---

def test_sequential_cas_uses_returned_sha():
    repo_root, tenant = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r1 = store.create_card(tenant, "card-seq", "desc seq",
                               "## Fact\nx\n\n## How to Verify\ny")
        sha1 = r1["git"]["detail"]
        assert sha1 == head_sha(repo_root), \
            "returned SHA must equal canonical HEAD after mutate"
        r2 = store.update_card(tenant, r1["id"], description="d2",
                               expected_base_sha=sha1)
        assert r2["git"]["committed"] is True
        assert is_working_tree_clean(repo_root), \
            "working tree must be clean after sequential CAS-chained mutations"
        sha2 = r2["git"]["detail"]
        assert sha2 == head_sha(repo_root), \
            "returned SHA must equal canonical HEAD after second mutate"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)