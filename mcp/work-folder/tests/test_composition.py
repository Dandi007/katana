"""composition contract tests: kernel + work-folder end-to-end.

Tests the full governed chain for work-folder: kernel.mutate -> policy -> CAS ->
VFS -> ledger -> manifest -> git commit, verifying invariants via git history,
working tree, resource_id behaviour, VFS governance, and domain policy.
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime

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
from katana_work_folder_mcp import lifecycle
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy


def _fixed_now():
    return datetime(2026, 6, 22, 9, 0, 0)


def _make_git_repo():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    with open(os.path.join(d, ".gitkeep"), "w") as f:
        f.write("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=d, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, check=True, capture_output=True)
    return d


def _setup_kernel_and_store(repo_root):
    kernel = GovernedKernel()
    vfs = GovernedVFS(repo_root)
    ledger = ResourceIdLedger(
        os.path.join(repo_root, ".katana", "tombstones.json"),
        prefix="wf-",
    )
    manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
    policy = _wf_policy()
    kernel.bind("work-folder", policy, vfs, ledger, manifest, repo_root)
    store = WorkFolderStore(kernel)
    return kernel, store


# --- 1. CAS: stale expected_base_sha rejected (spec L25) ---

def test_composition_cas_rejects_stale_sha():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create("test topic", _fixed_now)
        with pytest.raises(CASRejectionError):
            store.create("another topic", _fixed_now, expected_base_sha="a" * 40)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_cas_rejects_stale_sha_for_save():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        with pytest.raises(CASRejectionError):
            store.save(r["path"], _fixed_now, summary="bad save",
                       expected_base_sha="a" * 40)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 2. durable manifest in git history + working tree clean (spec L26) ---

def test_composition_manifest_in_git_history():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create("test topic", _fixed_now)
        assert result["git"]["committed"] is True
        manifest_id = result["manifest"]["manifest_id"]
        git_log = subprocess.run(
            ["git", "-C", repo_root, "log", "--oneline", "-n", "5"],
            capture_output=True, text=True,
        )
        assert "create" in git_log.stdout
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
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create("test topic", _fixed_now)
        manifest_id = result["manifest"]["manifest_id"]
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
                detail = git_field.get("detail", "")
                assert detail, "git.detail must be non-empty"
                assert len(detail) == 40, \
                    f"git.detail must be a valid 40-char SHA, got {detail!r}"
                subprocess.run(
                    ["git", "-C", repo_root, "cat-file", "-t", detail],
                    capture_output=True, check=True,
                )
                found = True
                break
        assert found, "manifest not found in committed git history"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_working_tree_clean_after_create():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create("test topic", _fixed_now)
        assert is_working_tree_clean(repo_root), "working tree not clean after create"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_working_tree_clean_after_save():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        store.save(r["path"], _fixed_now, summary="checkpoint",
                   context_snapshot="# Context\nsnapshot")
        assert is_working_tree_clean(repo_root), "working tree not clean after save"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_working_tree_clean_after_resume():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        store.resume(r["path"], _fixed_now)
        assert is_working_tree_clean(repo_root), "working tree not clean after resume"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_working_tree_clean_after_reindex():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create("test topic", _fixed_now)
        store.reindex(dry_run=False)
        assert is_working_tree_clean(repo_root), "working tree not clean after reindex"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 3. work-folder resource_id (wf- prefix) tombstone not reused (spec L27) ---

def test_composition_wf_resource_id_has_wf_prefix():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create("test topic", _fixed_now)
        assert result["created"] is True
        wf_id = result.get("id")
        assert wf_id is not None, "create must return a resource_id"
        assert wf_id.startswith("wf-"), \
            f"resource_id must start with wf-, got {wf_id!r}"
        assert len(wf_id) == 9, \
            f"resource_id must be wf-<6hex>, got {wf_id!r} (len={len(wf_id)})"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_resource_id_in_brief_file():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create("test topic", _fixed_now)
        wf_id = result["id"]
        brief_path = result["path"] + "/_brief.md"
        with open(os.path.join(repo_root, brief_path), encoding="utf-8") as f:
            content = f.read()
        from katana_work_folder_mcp.brief import parse_brief
        r = parse_brief(content)
        assert r["frontmatter"]["id"] == wf_id, \
            "resource_id in brief must match the one returned by create"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_resource_id_not_reused_force_collision():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        binding = kernel.get_binding("work-folder")
        tombstone_id = binding.ledger.gen_id(set())
        binding.ledger.tombstone(tombstone_id)
        assert binding.ledger.is_tombstoned(tombstone_id), \
            "tombstoned id must be recognised as tombstoned"
        for _ in range(50):
            new_id = binding.ledger.gen_id({tombstone_id})
            assert new_id != tombstone_id, \
                f"tombstoned id {tombstone_id} reused as {new_id}"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 4. governed VFS: reject path traversal / cross-domain write (spec L28) ---

def test_composition_vfs_rejects_path_traversal():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("work-folder").vfs
        with pytest.raises(Exception):
            vfs.write("../escape.md", "should not work")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_vfs_rejects_absolute_path():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("work-folder").vfs
        with pytest.raises(Exception):
            vfs.write("/tmp/escaped.txt", "absolute path")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_vfs_rejects_symlink():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("work-folder").vfs
        vfs.write("legit.md", "content")
        link_path = os.path.join(repo_root, "link.md")
        os.symlink(os.path.join(repo_root, "legit.md"), link_path)
        with pytest.raises(Exception):
            vfs.read_text("link.md")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 5. work-folder domain invariant enforce (spec L29) ---

def test_composition_wf_create_rejects_empty_topic():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        from katana_kernel.policy import PolicyViolationError
        with pytest.raises(PolicyViolationError, match="topic"):
            store.create("", _fixed_now)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_save_rejects_missing_folder_in_policy():
    repo_root = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        ledger = ResourceIdLedger(
            os.path.join(repo_root, ".katana", "tombstones.json"),
            prefix="wf-",
        )
        manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        policy = _wf_policy()
        kernel.bind("work-folder", policy, vfs, ledger, manifest, repo_root)
        from katana_kernel.policy import PolicyViolationError
        with pytest.raises(PolicyViolationError, match="folder"):
            kernel.mutate("work-folder", "wf_save", {})
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 6. single authoritative writer: second domain binding rejected (spec L30) ---

def test_composition_duplicate_domain_name_rejected():
    repo_root = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        policy = _wf_policy()
        ledger = ResourceIdLedger(
            os.path.join(repo_root, ".katana", "tombstones.json"),
            prefix="wf-",
        )
        manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        kernel.bind("work-folder", policy, vfs, ledger, manifest, repo_root)
        with pytest.raises(ValueError, match="already bound"):
            kernel.bind("work-folder", policy, vfs, ledger, manifest, repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_different_domain_same_repo_rejected():
    repo_root = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        wf_policy = _wf_policy()
        wf_ledger = ResourceIdLedger(
            os.path.join(repo_root, ".katana", "tombstones.json"),
            prefix="wf-",
        )
        wf_manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        kernel.bind("work-folder", wf_policy, vfs, wf_ledger, wf_manifest, repo_root)

        mem_policy = DomainPolicy(
            domain="memory",
            allowed_ops={"create", "update", "delete", "edit", "list", "get", "read"},
            invariants=[],
        )
        mem_ledger = ResourceIdLedger(
            os.path.join(repo_root, ".katana", "tombstones2.json"),
            prefix="m-",
        )
        mem_manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests2"))
        vfs2 = GovernedVFS(repo_root)
        with pytest.raises(ValueError, match="already bound"):
            kernel.bind("memory", mem_policy, vfs2, mem_ledger, mem_manifest, repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 7. 4 mutating tools through kernel with return contract unchanged (spec L31) ---

def test_composition_wf_create_return_contract():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.create("test topic", _fixed_now)
        assert result["created"] is True
        assert "path" in result
        assert "seeded" in result
        assert "drafting" in result
        assert "id" in result
        assert result["id"].startswith("wf-")
        assert "git" in result
        assert result["git"]["committed"] is True
        assert "manifest" in result
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_save_return_contract():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        result = store.save(r["path"], _fixed_now, summary="checkpoint",
                            context_snapshot="# Context\nsnapshot")
        assert result["saved"] is True
        assert "folder" in result
        assert "written" in result
        assert "contract" in result
        assert "git" in result
        assert result["git"]["committed"] is True
        assert "manifest" in result
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_resume_return_contract():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        result = store.resume(r["path"], _fixed_now)
        assert result["ok"] is True
        assert "folder" in result
        assert "loaded" in result
        assert "verification" in result
        assert "blocked" in result
        assert "resume_report" in result
        assert "contract" in result
        assert "git" in result
        assert result["git"]["committed"] is True
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_reindex_return_contract():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create("test topic", _fixed_now)
        result = store.reindex(dry_run=False)
        assert "indexed" in result
        assert result["indexed"] >= 1
        assert "skipped" in result
        assert "errors" in result
        assert "index_path" in result
        assert "git" in result
        assert result["git"]["committed"] is True
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_reindex_index_path_is_absolute():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create("test topic", _fixed_now)
        result = store.reindex(dry_run=False)
        index_path = result["index_path"]
        assert os.path.isabs(index_path), \
            f"index_path must be absolute, got {index_path!r}"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 8. sequential CAS uses returned SHA ---

def test_sequential_cas_uses_returned_sha():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r1 = store.create("test topic", _fixed_now)
        sha1 = r1["git"]["detail"]
        assert sha1 == head_sha(repo_root), \
            "returned SHA must equal canonical HEAD after mutate"

        r2 = store.save(r1["path"], _fixed_now, summary="second save",
                        expected_base_sha=sha1)
        assert r2["git"]["committed"] is True
        assert is_working_tree_clean(repo_root), \
            "working tree must be clean after sequential CAS-chained save"
        sha2 = r2["git"]["detail"]
        assert sha2 == head_sha(repo_root), \
            "returned SHA must equal canonical HEAD after second mutate"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 9. working tree clean after CAS rejection ---

def test_composition_working_tree_clean_after_cas_rejection():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        with pytest.raises(CASRejectionError):
            store.create("test topic", _fixed_now, expected_base_sha="a" * 40)
        assert is_working_tree_clean(repo_root), \
            "working tree must be clean after CAS rejection"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 10. wf_save appends only (golden-order, findings, changelog) ---

def test_composition_wf_save_append_only_golden_order():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        store.save(r["path"], _fixed_now, golden_order_additions="- 第一条\n")
        store.save(r["path"], _fixed_now, golden_order_additions="- 第二条\n")
        go_path = os.path.join(repo_root, r["path"], "golden-order.md")
        with open(go_path, encoding="utf-8") as f:
            content = f.read()
        assert "第一条" in content
        assert "第二条" in content
        assert content.count("第一条") == 1
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wf_save_append_only_changelog():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("test topic", _fixed_now)
        store.save(r["path"], _fixed_now, summary="save 1")
        store.save(r["path"], _fixed_now, summary="save 2")
        progress_path = os.path.join(repo_root, r["path"], "progress.md")
        with open(progress_path, encoding="utf-8") as f:
            content = f.read()
        assert content.count("save 1") == 1
        assert content.count("save 2") == 1
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 11. wf_save creates missing files on non-existent folder raises ---

def test_composition_wf_save_missing_folder_raises():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        with pytest.raises(FileNotFoundError):
            store.save("nonexistent/folder", _fixed_now)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 12. wf_reindex dry_run does not write ---

def test_composition_wf_reindex_dry_run_no_write():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.create("test topic", _fixed_now)
        result = store.reindex(dry_run=True)
        assert "preview" in result
        assert "# Work Folder INDEX" in result["preview"]
        assert not os.path.exists(os.path.join(repo_root, "INDEX.md"))
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 13. BROKEN blocks through governed store (M4 from feedback) ---

def test_composition_broken_blocks_through_store():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r = store.create("broken test", _fixed_now)
        wf_rel = r["path"]

        context = (
            "# Context\n\n"
            "**Updated:** 2026-06-22 14:00\n\n"
            "## 工作上下文\n- 集成测试\n\n"
            "## 关键路径\n"
            "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |\n"
            "|------|------------|------------|------|\n"
            "| target | /nonexistent/composition-broken-xyz | - | 探测目标 |\n\n"
            "## 环境信息\n- test\n"
        )
        store.save(wf_rel, _fixed_now, context_snapshot=context)

        result = store.resume(wf_rel, _fixed_now)
        assert result["ok"] is True
        assert result["verification"]["overall"] == "BROKEN"
        assert result["blocked"] is True
        assert result["contract"] == lifecycle.RESUME_BLOCKED_CONTRACT
        assert "/nonexistent/composition-broken-xyz" in result["resume_report"]
        assert "git" in result
        assert result["git"]["committed"] is True
        assert is_working_tree_clean(repo_root), "working tree must be clean after BROKEN resume"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 14. BROKEN blocks through governed store with missing folder (no-op) ---

def test_composition_broken_missing_folder_no_commit():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        sha_before = head_sha(repo_root)
        result = store.resume("nonexistent/folder", _fixed_now)
        assert result["ok"] is False
        assert result["blocked"] is True
        assert result.get("git", {}).get("committed") is not True
        sha_after = head_sha(repo_root)
        assert sha_before == sha_after, "no-op resume should not create a commit"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)