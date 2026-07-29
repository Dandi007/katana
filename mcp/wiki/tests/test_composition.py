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
from katana_wiki_mcp.pages import parse_page, render_page


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


def _existing_page(path="Zettelkasten/既有概念.md", page_id="w-a1b2c3"):
    return {
        "path": path,
        "frontmatter": {
            "id": page_id,
            "创建日期": "2026-06-20 09:00",
            "tags": ["test"],
            "类型": "卡片",
            "sources": ["human:原始来源"],
            "摘要": "一个既有测试概念",
        },
        "body": "旧正文 [[测试页]]\n",
        "back_updates": [],
    }


def _seed_page(repo_root, page):
    path = os.path.join(repo_root, page["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_page(page["frontmatter"], page["body"]))
    subprocess.run(["git", "-C", repo_root, "add", "--", page["path"]], check=True)
    subprocess.run(
        ["git", "-C", repo_root, "commit", "-m", "seed existing page"],
        check=True, capture_output=True,
    )


def _seed_raw_page(repo_root, path, content):
    abs_path = os.path.join(repo_root, path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run(["git", "-C", repo_root, "add", "--", path], check=True)
    subprocess.run(
        ["git", "-C", repo_root, "commit", "-m", "seed malformed page"],
        check=True, capture_output=True,
    )


def _valid_update(path="Zettelkasten/既有概念.md", page_id="w-a1b2c3"):
    page = _existing_page(path, page_id)
    page["frontmatter"]["sources"] = ["human:原始来源", "conversation 2026-07-29"]
    page["frontmatter"]["摘要"] = "已更新的既有测试概念"
    page["body"] = "新正文 [[测试页]]\n"
    return {"updates": [page], "log_line": "## [2026-07-29 13:20] ingest | update"}


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


# --- 9. explicit existing-page update contract ---

def test_ingest_update_preserves_id_and_path_and_records_log():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)

        result = store.ingest_apply(
            _valid_update(), expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is True
        assert result["created"] == []
        assert result["updated"] == [existing["path"]]
        assert result["written"] == [existing["path"]]
        content = kernel.get_binding("wiki").vfs.read_text(existing["path"])
        fm, body = parse_page(content)
        assert fm["id"] == existing["frontmatter"]["id"]
        assert body == "新正文 [[测试页]]\n"
        assert "ingest | update" in kernel.get_binding("wiki").vfs.read_text("log.md")
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_requires_expected_base_sha_with_zero_writes():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        content_before = kernel.get_binding("wiki").vfs.read_text(existing["path"])
        head_before = head_sha(repo_root)

        result = store.ingest_apply(_valid_update())

        assert result["applied"] is False
        assert "updates require expected_base_sha from wiki_ingest_plan" in \
            result["rejected"]["(proposal)"]
        assert kernel.get_binding("wiki").vfs.read_text(existing["path"]) == content_before
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_rejects_missing_id_with_zero_writes():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        del proposal["updates"][0]["frontmatter"]["id"]
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any("id" in e for e in result["rejected"][existing["path"]])
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_rejects_id_path_mismatch_with_zero_writes():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update(page_id="w-dead00")
        head_before = head_sha(repo_root)
        content_before = kernel.get_binding("wiki").vfs.read_text(existing["path"])

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any("mismatch" in e for e in result["rejected"][existing["path"]])
        assert kernel.get_binding("wiki").vfs.read_text(existing["path"]) == content_before
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


@pytest.mark.parametrize(
    ("content", "expected_error"),
    [
        (
            "---\nid: w-a1b2c3\ntags: [broken\n---\n正文 [[测试页]]\n",
            "解析失败",
        ),
        (
            "---\n- id\n- w-a1b2c3\n---\n正文 [[测试页]]\n",
            "must be a mapping",
        ),
    ],
)
def test_ingest_update_rejects_malformed_existing_frontmatter(
    content, expected_error
):
    repo_root = _make_git_repo()
    path = "Zettelkasten/既有概念.md"
    try:
        _seed_raw_page(repo_root, path, content)
        _, store = _setup_kernel_and_store(repo_root)
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            _valid_update(), expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any(expected_error in e for e in result["rejected"][path])
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_new_page_rejects_existing_path_with_zero_writes():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["path"] = existing["path"]
        head_before = head_sha(repo_root)
        content_before = kernel.get_binding("wiki").vfs.read_text(existing["path"])

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any("已存在" in e for e in result["rejected"][existing["path"]])
        assert kernel.get_binding("wiki").vfs.read_text(existing["path"]) == content_before
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_rejects_nonexistent_path_with_zero_writes():
    repo_root = _make_git_repo()
    try:
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update(path="Zettelkasten/不存在.md")
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any("不存在" in e for e in result["rejected"]["Zettelkasten/不存在.md"])
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_rejects_same_path_in_create_and_update():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        new_page = _valid_proposal()["new_pages"][0]
        new_page["path"] = existing["path"]
        proposal["new_pages"] = [new_page]
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any("重复" in e for e in result["rejected"][existing["path"]])
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_new_page_rejects_caller_supplied_id():
    repo_root = _make_git_repo()
    try:
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["frontmatter"]["id"] = "w-a1b2c3"

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        errors = result["rejected"]["Zettelkasten/新概念.md"]
        assert any("不得指定 id" in e for e in errors)
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_canonicalizes_before_duplicate_detection():
    repo_root = _make_git_repo()
    try:
        _, store = _setup_kernel_and_store(repo_root)
        first = _valid_proposal()["new_pages"][0]
        second = _valid_proposal()["new_pages"][0]
        first["path"] = "A.md"
        second["path"] = "./A.md"
        proposal = {
            "new_pages": [first, second],
            "log_line": "## duplicate canonical path",
        }
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any("重复" in e for e in result["rejected"]["A.md"])
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_parent_traversal():
    repo_root = _make_git_repo()
    try:
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["path"] = "../escape.md"
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "inside wiki_root" in e
            for e in result["rejected"]["../escape.md"]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


@pytest.mark.parametrize("character", ["\x00", "\n", "\t", "\x7f"])
def test_ingest_store_rejects_control_character_path_with_zero_writes(character):
    repo_root = _make_git_repo()
    try:
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        path = f"Zettelkasten/坏{character}页.md"
        proposal["new_pages"][0]["path"] = path
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "control characters" in error
            for error in result["rejected"][path]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_symlink_path():
    repo_root = _make_git_repo()
    outside = tempfile.mkdtemp()
    try:
        os.symlink(outside, os.path.join(repo_root, "link"))
        subprocess.run(["git", "-C", repo_root, "add", "link"], check=True)
        subprocess.run(
            ["git", "-C", repo_root, "commit", "-m", "seed symlink"],
            check=True, capture_output=True,
        )
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["path"] = "link/escape.md"
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "symlink" in e for e in result["rejected"]["link/escape.md"]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)


def test_ingest_store_rejects_non_string_log_line_before_write():
    repo_root = _make_git_repo()
    try:
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["log_line"] = {"not": "a string"}
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert "log_line must be a string" in result["rejected"]["(proposal)"]
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


@pytest.mark.parametrize("target", ["target-dir", ".gitignore"])
def test_ingest_store_rejects_non_page_backlink_target(target):
    repo_root = _make_git_repo()
    try:
        target_path = os.path.join(repo_root, target)
        if target == "target-dir":
            os.mkdir(target_path)
        else:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write("*.tmp\n")
            subprocess.run(["git", "-C", repo_root, "add", ".gitignore"], check=True)
            subprocess.run(
                ["git", "-C", repo_root, "commit", "-m", "seed gitignore"],
                check=True, capture_output=True,
            )
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["back_updates"] = [
            {"path": target, "title": "新概念"}
        ]
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "governed wiki page" in error or "regular .md" in error
            for error in result["rejected"]["Zettelkasten/新概念.md"]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_existing_nfc_path_collision():
    repo_root = _make_git_repo()
    try:
        _seed_page(repo_root, _existing_page("é.md", "w-a1b2c3"))
        _seed_page(repo_root, _existing_page("e\u0301.md", "w-b2c3d4"))
        _, store = _setup_kernel_and_store(repo_root)
        head_before = head_sha(repo_root)

        result = store.ingest_apply(_valid_proposal())

        assert result["applied"] is False
        assert any(
            "existing NFC path collisions" in error
            for error in result["rejected"]["(proposal)"]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("---\nid: w-a1b2c3\n正文无 closing delimiter\n", "paired"),
        ("---\n{}\n---\n空 frontmatter\n", "frontmatter invalid"),
    ],
)
def test_ingest_store_rejects_backlink_target_without_page_identity(
    content, expected
):
    repo_root = _make_git_repo()
    try:
        _seed_raw_page(repo_root, "target.md", content)
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["back_updates"] = [
            {"path": "target.md", "title": "新概念"}
        ]
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            expected in error
            for error in result["rejected"]["Zettelkasten/新概念.md"]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_ignores_nfc_collisions_in_raw_zone():
    repo_root = _make_git_repo()
    try:
        raw_content = "---\ntitle: raw source\n---\nraw\n"
        _seed_raw_page(repo_root, "raw/é.md", raw_content)
        _seed_raw_page(repo_root, "raw/e\u0301.md", raw_content)
        _, store = _setup_kernel_and_store(repo_root)

        result = store.ingest_apply(_valid_proposal())

        assert result["applied"] is True
        assert result["created"] == ["Zettelkasten/新概念.md"]
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_deepthought_precious_overwrite_zero_dirty():
    repo_root = _make_git_repo()
    original = b"PRECIOUS RAW BYTES\n\x00\x01"
    try:
        precious_path = "DeepThought/PRECIOUS.md"
        precious = os.path.join(repo_root, precious_path)
        os.makedirs(os.path.dirname(precious), exist_ok=True)
        with open(precious, "wb") as f:
            f.write(original)
        subprocess.run(["git", "-C", repo_root, "add", precious_path], check=True)
        subprocess.run(
            ["git", "-C", repo_root, "commit", "-m", "seed precious raw"],
            check=True, capture_output=True,
        )
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["path"] = precious_path
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "governed writable" in error
            for error in result["rejected"][precious_path]
        )
        with open(precious, "rb") as f:
            assert f.read() == original
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_raw_duplicate_ids_do_not_block_governed_ingest():
    repo_root = _make_git_repo()
    try:
        raw_page = _existing_page("raw/a.md", "w-a1b2c3")
        _seed_page(repo_root, raw_page)
        _seed_page(repo_root, _existing_page("raw/b.md", "w-a1b2c3"))
        _, store = _setup_kernel_and_store(repo_root)

        result = store.ingest_apply(_valid_proposal())

        assert result["applied"] is True
        assert result["created"] == ["Zettelkasten/新概念.md"]
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_updates_legacy_page_without_adding_id():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        existing["frontmatter"].pop("id")
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        proposal["updates"][0]["frontmatter"].pop("id")

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is True
        content = kernel.get_binding("wiki").vfs.read_text(existing["path"])
        fm, body = parse_page(content)
        assert "id" not in fm
        assert body == "新正文 [[测试页]]\n"
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_forged_id_on_legacy_page():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        existing["frontmatter"].pop("id")
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        content_before = kernel.get_binding("wiki").vfs.read_text(existing["path"])
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            _valid_update(), expected_base_sha=head_before
        )

        assert result["applied"] is False
        assert any(
            "must not add or forge id" in error
            for error in result["rejected"][existing["path"]]
        )
        assert kernel.get_binding("wiki").vfs.read_text(existing["path"]) == content_before
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_existing_png_new_page_zero_dirty():
    repo_root = _make_git_repo()
    original = b"\x89PNG\r\nPRECIOUS"
    try:
        asset_path = "Zettelkasten/asset.png"
        asset = os.path.join(repo_root, asset_path)
        os.makedirs(os.path.dirname(asset), exist_ok=True)
        with open(asset, "wb") as f:
            f.write(original)
        subprocess.run(["git", "-C", repo_root, "add", asset_path], check=True)
        subprocess.run(
            ["git", "-C", repo_root, "commit", "-m", "seed png"],
            check=True, capture_output=True,
        )
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        proposal["new_pages"][0]["path"] = asset_path
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "must end with .md" in error
            for error in result["rejected"][asset_path]
        )
        with open(asset, "rb") as f:
            assert f.read() == original
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_store_rejects_regular_file_path_ancestor_zero_dirty():
    repo_root = _make_git_repo()
    original = b"PARENT FILE BYTES\n"
    try:
        parent_path = "Zettelkasten/parent"
        parent = os.path.join(repo_root, parent_path)
        os.makedirs(os.path.dirname(parent), exist_ok=True)
        with open(parent, "wb") as f:
            f.write(original)
        subprocess.run(["git", "-C", repo_root, "add", parent_path], check=True)
        subprocess.run(
            ["git", "-C", repo_root, "commit", "-m", "seed parent file"],
            check=True, capture_output=True,
        )
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_proposal()
        child_path = "Zettelkasten/parent/child.md"
        proposal["new_pages"][0]["path"] = child_path
        head_before = head_sha(repo_root)

        result = store.ingest_apply(proposal)

        assert result["applied"] is False
        assert any(
            "ancestor must be a non-symlink directory" in error
            for error in result["rejected"][child_path]
        )
        with open(parent, "rb") as f:
            assert f.read() == original
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_rejects_missing_backlink_target_before_any_write():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        proposal["updates"][0]["back_updates"] = [
            {"path": "Zettelkasten/不存在的反链目标.md", "title": "既有概念"}
        ]
        content_before = kernel.get_binding("wiki").vfs.read_text(existing["path"])
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        assert any("back_update path 不存在" in e or "regular .md" in e
                   for e in result["rejected"][existing["path"]])
        assert kernel.get_binding("wiki").vfs.read_text(existing["path"]) == content_before
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_rejects_duplicate_existing_id():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        duplicate = _existing_page("Zettelkasten/重复ID.md", "w-a1b2c3")
        _seed_page(repo_root, existing)
        _seed_page(repo_root, duplicate)
        _, store = _setup_kernel_and_store(repo_root)
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            _valid_update(), expected_base_sha=head_before
        )

        assert result["applied"] is False
        assert any(
            "existing duplicate ids" in error
            for error in result["rejected"]["(proposal)"]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_rejects_source_removal():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        existing["frontmatter"]["sources"].append("human:不可丢失来源")
        _seed_page(repo_root, existing)
        _, store = _setup_kernel_and_store(repo_root)
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            _valid_update(), expected_base_sha=head_before
        )

        assert result["applied"] is False
        assert any(
            "sources must be a superset" in error
            for error in result["rejected"][existing["path"]]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_rejects_untrusted_backlink_title():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        target = _existing_page("Zettelkasten/关联页.md", "w-b2c3d4")
        _seed_page(repo_root, existing)
        _seed_page(repo_root, target)
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        proposal["updates"][0]["back_updates"] = [
            {"path": target["path"], "title": "任意注入标题"}
        ]
        head_before = head_sha(repo_root)

        result = store.ingest_apply(
            proposal, expected_base_sha=head_before
        )

        assert result["applied"] is False
        assert any(
            "must equal proposal page title" in error
            for error in result["rejected"][existing["path"]]
        )
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_keeps_provenance_frontmatter_outlink_validation():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        _, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        del proposal["updates"][0]["frontmatter"]["sources"]
        del proposal["updates"][0]["frontmatter"]["摘要"]
        proposal["updates"][0]["body"] = "没有 outlink\n"

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is False
        errors = result["rejected"][existing["path"]]
        assert any("sources" in e for e in errors)
        assert any("摘要" in e for e in errors)
        assert any("outlink" in e or "孤岛" in e for e in errors)
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_writes_backlink_and_log_in_same_commit():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        target = _existing_page("Zettelkasten/关联页.md", "w-b2c3d4")
        _seed_page(repo_root, existing)
        _seed_page(repo_root, target)
        kernel, store = _setup_kernel_and_store(repo_root)
        proposal = _valid_update()
        proposal["updates"][0]["back_updates"] = [
            {"path": target["path"], "title": "既有概念"}
        ]

        result = store.ingest_apply(
            proposal, expected_base_sha=head_sha(repo_root)
        )

        assert result["applied"] is True
        assert result["backlinked"] == [target["path"]]
        backlink_content = kernel.get_binding("wiki").vfs.read_text(target["path"])
        assert "[[既有概念]]" in backlink_content
        changed = subprocess.run(
            [
                "git", "-C", repo_root, "-c", "core.quotepath=false",
                "show", "--name-only", "--format=", "HEAD",
            ],
            check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        assert existing["path"] in changed
        assert target["path"] in changed
        assert "log.md" in changed
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_cas_conflict_has_zero_disk_changes():
    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        kernel, store = _setup_kernel_and_store(repo_root)
        head_before = head_sha(repo_root)
        content_before = kernel.get_binding("wiki").vfs.read_text(existing["path"])

        with pytest.raises(CASRejectionError):
            store.ingest_apply(_valid_update(), expected_base_sha="a" * 40)

        assert kernel.get_binding("wiki").vfs.read_text(existing["path"]) == content_before
        assert head_sha(repo_root) == head_before
        assert is_working_tree_clean(repo_root)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)


def test_ingest_update_requires_kernel_second_head_gate_against_toctou(monkeypatch):
    """记录 wiki 组合层对 kernel CAS TOCTOU 防护的显式依赖。"""
    import katana_kernel.kernel as kernel_module

    repo_root = _make_git_repo()
    try:
        existing = _existing_page()
        _seed_page(repo_root, existing)
        _, store = _setup_kernel_and_store(repo_root)
        expected_head = head_sha(repo_root)
        original_cas_guard = kernel_module.cas_guard

        def race_after_first_head_check(repo, expected):
            original_cas_guard(repo, expected)
            marker = os.path.join(repo, "concurrent.md")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("concurrent writer\n")
            subprocess.run(["git", "-C", repo, "add", "concurrent.md"], check=True)
            subprocess.run(
                ["git", "-C", repo, "commit", "-m", "concurrent writer"],
                check=True, capture_output=True,
            )

        monkeypatch.setattr(kernel_module, "cas_guard", race_after_first_head_check)

        with pytest.raises(CASRejectionError):
            store.ingest_apply(_valid_update(), expected_base_sha=expected_head)
    finally:
        import shutil
        shutil.rmtree(repo_root, ignore_errors=True)
