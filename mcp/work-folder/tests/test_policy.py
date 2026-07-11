"""Work Folder domain policy tests (design §5.6)."""
import pytest

from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError

from katana_work_folder_mcp import brief as _brief
from katana_work_folder_mcp.policy import ID_PREFIX, WorkFolderPolicy

GOOD_BRIEF = _brief.render_brief(
    id="wf-abc123", title="t", status="active",
    created="2026-07-11", updated="2026-07-11", goal="do x", summary="s")


def _batch(content, path, op=Op.CREATE):
    b = MutationBatch(domain="work-folder")
    b.add(Change(op=op, resource_id="wf-abc123", after_path=path,
                 after_content=content.encode("utf-8")))
    return b


def test_valid_brief_passes():
    WorkFolderPolicy().validate(_batch(GOOD_BRIEF, "2026/07/11/x/_brief.md"))


def test_malformed_brief_rejected():
    with pytest.raises(KernelError) as ei:
        WorkFolderPolicy().validate(
            _batch("no frontmatter\n", "2026/07/11/x/_brief.md"))
    assert ei.value.code == INVALID_CONTENT
    assert ei.value.violations


def test_non_control_document_not_gated():
    # progress notes / documents are not schema-gated.
    WorkFolderPolicy().validate(_batch("free-form notes\n",
                                       "2026/07/11/x/progress.md"))


def test_delete_skips_validation():
    b = MutationBatch(domain="work-folder")
    b.add(Change(op=Op.DELETE, resource_id="wf-abc123",
                 before_path="2026/07/11/x/_brief.md"))
    WorkFolderPolicy().validate(b)


def test_policy_identity():
    p = WorkFolderPolicy()
    assert p.domain == "work-folder" and p.id_prefix == ID_PREFIX == "wf-"
