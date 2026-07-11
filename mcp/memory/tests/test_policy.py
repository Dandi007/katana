"""Memory domain policy hard-invariant tests (design §5.6)."""
import pytest

from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError

from katana_memory_mcp.policy import ID_PREFIX, MemoryPolicy

VALID = (
    "---\nid: m-abc123\nname: a-card\ndescription: d\nstatus: active\n"
    "---\n\n## Fact\nx\n\n## How to Verify\ny\n"
)


def _batch(content: str, path: str = "uther/a-card.md", op=Op.CREATE):
    b = MutationBatch(domain="memory")
    b.add(Change(op=op, resource_id="m-abc123", after_path=path,
                 after_content=content.encode("utf-8")))
    return b


def test_valid_card_passes():
    MemoryPolicy().validate(_batch(VALID))


def test_unparseable_frontmatter_rejected():
    with pytest.raises(KernelError) as ei:
        MemoryPolicy().validate(_batch("no frontmatter here\n"))
    assert ei.value.code == INVALID_CONTENT


def test_missing_id_rejected():
    bad = "---\nname: a\ndescription: d\n---\nbody\n"
    with pytest.raises(KernelError):
        MemoryPolicy().validate(_batch(bad))


def test_wrong_prefix_rejected():
    bad = VALID.replace("m-abc123", "x-abc123")
    with pytest.raises(KernelError) as ei:
        MemoryPolicy().validate(_batch(bad))
    assert ei.value.code == INVALID_CONTENT


def test_delete_change_skips_content_check():
    b = MutationBatch(domain="memory")
    b.add(Change(op=Op.DELETE, resource_id="m-abc123", before_path="uther/a.md"))
    MemoryPolicy().validate(b)  # no raise


def test_name_filename_mismatch_rejected():
    with pytest.raises(KernelError) as ei:
        MemoryPolicy().validate(_batch(VALID, path="uther/wrong-name.md"))
    assert ei.value.code == INVALID_CONTENT
    assert "name/filename mismatch" in ei.value.violations


def test_id_immutable_across_update():
    before = VALID.encode("utf-8")
    after = VALID.replace("m-abc123", "m-999999")
    b = MutationBatch(domain="memory")
    b.add(Change(op=Op.WRITE, resource_id="m-abc123",
                 after_path="uther/a-card.md",
                 after_content=after.encode("utf-8"),
                 before_content=before))
    with pytest.raises(KernelError) as ei:
        MemoryPolicy().validate(b)
    assert ei.value.code == INVALID_CONTENT
    assert "id changed" in ei.value.violations


def test_policy_identity_metadata():
    p = MemoryPolicy()
    assert p.domain == "memory"
    assert p.id_prefix == ID_PREFIX == "m-"
