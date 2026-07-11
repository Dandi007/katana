"""Governed-pipeline anchors for Work Folder domain tools (design §4.4, INV-5).

Proves wf_create / wf_save produce governed transactions (kernel manifest),
that wf_reindex's canonical INDEX.md write is governed, and that
WorkFolderPolicy hard invariants (brief validity, id immutability,
golden-order append-only) are enforced on the projected post-state.
"""
import asyncio
import subprocess

import pytest
from fastmcp import Client

import katana_work_folder_mcp.server as server
from katana_work_folder_mcp import brief as _brief
from katana_work_folder_mcp.policy import WorkFolderPolicy
from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError
from katana_kb_mcp_shared.kernel.manifest import extract_from_message


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def configured(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True,
                       capture_output=True, text=True)
    git("init", "-q")
    git("config", "user.email", "ci@ci")
    git("config", "user.name", "ci")
    (tmp_path / "seed.md").write_text("seed\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "init")
    server.configure(str(tmp_path), str(tmp_path))
    return tmp_path


def _head_message(repo):
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"],
                          capture_output=True, text=True).stdout


def test_wf_create_produces_governed_transaction(configured):
    head_before = subprocess.run(
        ["git", "-C", str(configured), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    created = _run(server.wf_create("治理管线 测试"))
    assert created["created"] is True
    head_after = subprocess.run(
        ["git", "-C", str(configured), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    assert head_after != head_before, "wf_create did not commit through kernel"
    manifest = extract_from_message(_head_message(configured))
    assert manifest is not None, "wf_create commit has no kernel manifest"
    assert manifest.domain == "work-folder"
    touched = {c["after_path"] for c in manifest.changes}
    assert any(p and p.endswith("_brief.md") for p in touched)


def test_wf_save_produces_governed_transaction(configured):
    folder = _run(server.wf_create("保存 测试"))["path"]
    _run(server.wf_save(folder, summary="checkpoint one"))
    manifest = extract_from_message(_head_message(configured))
    assert manifest is not None
    touched = {c["after_path"] for c in manifest.changes}
    assert any(p and p.endswith("progress.md") for p in touched)


def test_wf_reindex_index_write_is_governed(configured):
    _run(server.wf_create("索引 测试"))
    _run(server.wf_reindex())
    manifest = extract_from_message(_head_message(configured))
    assert manifest is not None
    touched = {c["after_path"] for c in manifest.changes}
    assert any(p and p.endswith("INDEX.md") for p in touched)


# ── WorkFolderPolicy hard invariants (design §5.6) ────────────────────

def _brief_batch(text, path="2026/07/11/x/_brief.md", before=None):
    b = MutationBatch(domain="work-folder")
    b.add(Change(op=Op.WRITE, resource_id="wf-1", after_path=path,
                 after_content=text.encode("utf-8"),
                 before_content=before.encode("utf-8") if before else None))
    return b


def test_brief_id_is_immutable():
    before = _brief.render_brief(id="wf-aaa", title="t", status="active",
                                 created="2026-07-11", updated="2026-07-11",
                                 goal="g", summary="s")
    after = _brief.render_brief(id="wf-bbb", title="t", status="active",
                                created="2026-07-11", updated="2026-07-12",
                                goal="g", summary="s")
    with pytest.raises(KernelError) as ei:
        WorkFolderPolicy().validate(_brief_batch(after, before=before))
    assert ei.value.code == INVALID_CONTENT
    assert "brief id changed" in ei.value.violations


def test_golden_order_is_append_only():
    before = "- 用户拍板 A\n".encode("utf-8")
    after = "- 完全不同的内容\n"  # not a superset of before → rejected
    b = MutationBatch(domain="work-folder")
    b.add(Change(op=Op.WRITE, resource_id="",
                 after_path="2026/07/11/x/golden-order.md",
                 after_content=after.encode("utf-8"), before_content=before))
    with pytest.raises(KernelError) as ei:
        WorkFolderPolicy().validate(b)
    assert "golden-order not append-only" in ei.value.violations


def test_golden_order_append_passes():
    before = "- 用户拍板 A\n".encode("utf-8")
    after = "- 用户拍板 A\n- 用户拍板 B\n"  # extends before → ok
    b = MutationBatch(domain="work-folder")
    b.add(Change(op=Op.WRITE, resource_id="",
                 after_path="2026/07/11/x/golden-order.md",
                 after_content=after.encode("utf-8"), before_content=before))
    WorkFolderPolicy().validate(b)  # no raise
