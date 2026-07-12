"""composition contract tests: kernel + wiki end-to-end.

Tests the full governed chain for wiki: kernel.mutate -> policy -> CAS ->
VFS -> ledger -> manifest -> git commit, verifying invariants via git history,
working tree, resource_id behaviour, VFS governance, and domain policy.
"""

import json
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
from katana_wiki_mcp.store import WikiStore, _wiki_policy


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
        prefix="w-",
    )
    manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
    policy = _wiki_policy()
    kernel.bind("wiki", policy, vfs, ledger, manifest, repo_root)
    store = WikiStore(kernel)
    return kernel, store


def _valid_proposal():
    return {
        "new_pages": [{
            "path": "Zettelkasten/新概念.md",
            "frontmatter": {
                "创建日期": "2026-06-22 10:00",
                "tags": ["test"],
                "类型": "卡片",
                "sources": ["human:测试"],
                "摘要": "一个测试概念",
            },
            "body": "正文 [[测试页]]\n",
            "back_updates": [],
        }],
        "log_line": "## [2026-06-22 10:00] ingest | test",
    }


# --- 1. CAS: stale expected_base_sha rejected (spec L25) ---

def test_composition_cas_rejects_stale_sha():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.ingest_apply(_valid_proposal())
        with pytest.raises(CASRejectionError):
            store.ingest_apply(_valid_proposal(), expected_base_sha="a" * 40)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 2. durable manifest in git history + working tree clean (spec L26) ---

def test_composition_manifest_in_git_history():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.ingest_apply(_valid_proposal())
        assert result["git"]["committed"] is True
        manifest_id = result["manifest"]["manifest_id"]
        git_log = subprocess.run(
            ["git", "-C", repo_root, "log", "--oneline", "-n", "5"],
            capture_output=True, text=True,
        )
        assert "ingest" in git_log.stdout
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
        result = store.ingest_apply(_valid_proposal())
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
                assert len(detail) == 40, f"git.detail must be a valid 40-char SHA, got {detail!r}"
                cat = subprocess.run(
                    ["git", "-C", repo_root, "cat-file", "-t", detail],
                    capture_output=True, text=True,
                )
                assert cat.returncode == 0, \
                    f"git.detail SHA {detail} not a valid git object in repo"
                found = True
                break
        assert found, "manifest not found in committed git history"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_working_tree_clean_after_ingest():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        store.ingest_apply(_valid_proposal())
        assert is_working_tree_clean(repo_root), "working tree not clean after ingest"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 3. wiki resource_id (w- prefix) tombstone not reused (spec L27) ---

def test_composition_wiki_resource_id_has_w_prefix():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        result = store.ingest_apply(_valid_proposal())
        assert result["applied"] is True
        page_path = os.path.join(repo_root, "Zettelkasten", "新概念.md")
        with open(page_path, encoding="utf-8") as f:
            content = f.read()
        from katana_wiki_mcp.pages import parse_page
        fm, _ = parse_page(content)
        page_id = fm.get("id")
        assert page_id is not None, "page must have a resource_id"
        assert page_id.startswith("w-"), f"resource_id must start with w-, got {page_id!r}"
        assert len(page_id) == 8, f"resource_id must be w-<6hex>, got {page_id!r}"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_wiki_resource_id_not_reused_force_collision():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        binding = kernel.get_binding("wiki")
        # Force-generate ids to verify no collision with the prefix
        ids = set()
        for _ in range(50):
            new_id = binding.ledger.gen_id(ids)
            assert new_id.startswith("w-"), f"id must start with w-, got {new_id!r}"
            assert new_id not in ids, f"collision: {new_id}"
            ids.add(new_id)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 4. governed VFS: reject path traversal / cross-domain write (spec L28) ---

def test_composition_vfs_rejects_path_traversal():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("wiki").vfs
        with pytest.raises(Exception):
            vfs.write("../escape.md", "should not work")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_vfs_rejects_absolute_path():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("wiki").vfs
        with pytest.raises(Exception):
            vfs.write("/tmp/escaped.txt", "absolute path")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_vfs_rejects_symlink():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        vfs = kernel.get_binding("wiki").vfs
        vfs.write("legit.md", "content")
        link_path = os.path.join(repo_root, "link.md")
        os.symlink(os.path.join(repo_root, "legit.md"), link_path)
        with pytest.raises(Exception):
            vfs.read_text("link.md")
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 5. wiki domain invariant enforce (spec L29) ---

def test_composition_ingest_rejects_missing_provenance():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        bad = {
            "new_pages": [{
                "path": "无来源.md",
                "frontmatter": {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片"},
                "body": "无来源也无外链的正文\n",
                "back_updates": [],
            }],
            "log_line": "## ingest",
        }
        result = store.ingest_apply(bad)
        assert result["applied"] is False
        assert "无来源.md" in result["rejected"]
        assert any("sources" in e or "provenance" in e
                   for e in result["rejected"]["无来源.md"])
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_ingest_rejects_missing_outlink():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        bad = {
            "new_pages": [{
                "path": "孤岛.md",
                "frontmatter": {
                    "创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片",
                    "sources": ["human:test"], "摘要": "一个孤岛",
                },
                "body": "没有任何 wikilink 的正文\n",
                "back_updates": [],
            }],
            "log_line": "## ingest",
        }
        result = store.ingest_apply(bad)
        assert result["applied"] is False
        assert "孤岛.md" in result["rejected"]
        assert any("孤岛" in e or "outlink" in e for e in result["rejected"]["孤岛.md"])
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_ingest_rejects_missing_summary():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        bad = {
            "new_pages": [{
                "path": "无摘要.md",
                "frontmatter": {
                    "创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片",
                    "sources": ["human:test"],
                },
                "body": "正文 [[有链接]]\n",
                "back_updates": [],
            }],
            "log_line": "## ingest",
        }
        result = store.ingest_apply(bad)
        assert result["applied"] is False
        assert "无摘要.md" in result["rejected"]
        assert any("摘要" in e for e in result["rejected"]["无摘要.md"])
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 6. single authoritative writer: second domain binding rejected (spec L30) ---

def test_composition_duplicate_domain_name_rejected():
    repo_root = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        policy = _wiki_policy()
        ledger = ResourceIdLedger(
            os.path.join(repo_root, ".katana", "tombstones.json"),
            prefix="w-",
        )
        manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        kernel.bind("wiki", policy, vfs, ledger, manifest, repo_root)
        with pytest.raises(ValueError, match="already bound"):
            kernel.bind("wiki", policy, vfs, ledger, manifest, repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_composition_different_domain_same_repo_rejected():
    repo_root = _make_git_repo()
    try:
        kernel = GovernedKernel()
        vfs = GovernedVFS(repo_root)
        wiki_policy = _wiki_policy()
        wiki_ledger = ResourceIdLedger(
            os.path.join(repo_root, ".katana", "tombstones.json"),
            prefix="w-",
        )
        wiki_manifest = TransactionManifest(os.path.join(repo_root, ".katana", "manifests"))
        kernel.bind("wiki", wiki_policy, vfs, wiki_ledger, wiki_manifest, repo_root)

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


# --- 7. sequential CAS uses returned SHA ---

def test_sequential_cas_uses_returned_sha():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        r1 = store.ingest_apply(_valid_proposal())
        sha1 = r1["git"]["detail"]
        assert sha1 == head_sha(repo_root), \
            "returned SHA must equal canonical HEAD after mutate"

        prop2 = _valid_proposal()
        prop2["new_pages"][0]["path"] = "Zettelkasten/另一概念.md"
        r2 = store.ingest_apply(prop2, expected_base_sha=sha1)
        assert r2["git"]["committed"] is True
        assert is_working_tree_clean(repo_root), \
            "working tree must be clean after sequential CAS-chained ingest"
        sha2 = r2["git"]["detail"]
        assert sha2 == head_sha(repo_root), \
            "returned SHA must equal canonical HEAD after second mutate"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


# --- 8. working tree clean after CAS rejection ---

def test_composition_working_tree_clean_after_cas_rejection():
    repo_root = _make_git_repo()
    try:
        kernel, store = _setup_kernel_and_store(repo_root)
        with pytest.raises(CASRejectionError):
            store.ingest_apply(_valid_proposal(), expected_base_sha="a" * 40)
        assert is_working_tree_clean(repo_root), \
            "working tree must be clean after CAS rejection"
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)