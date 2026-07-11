"""Wiki domain policy tests (design §5.6): schema hard-gate + raw immutability."""
import pytest

from katana_kb_mcp_shared.kernel.batch import Change, MutationBatch, Op
from katana_kb_mcp_shared.kernel.errors import INVALID_CONTENT, KernelError

from katana_wiki_mcp.policy import ID_PREFIX, WikiPolicy

GOOD_PAGE = (
    "---\n创建日期: 2026-07-11\ntags:\n  - x\n类型: 卡片\n摘要: 一行摘要\n"
    "---\n正文 [[某概念]]\n\n# References\n- src\n"
)
BAD_PAGE = "---\n类型: 卡片\n---\n正文没有 outlink\n"


def _batch(content, path="Zettelkasten/a.md", op=Op.CREATE):
    b = MutationBatch(domain="wiki")
    b.add(Change(op=op, resource_id="w-abc123", after_path=path,
                 after_content=content.encode("utf-8")))
    return b


def test_valid_governed_page_passes():
    WikiPolicy().validate(_batch(GOOD_PAGE))


def test_typed_page_missing_outlink_rejected():
    with pytest.raises(KernelError) as ei:
        WikiPolicy().validate(_batch(BAD_PAGE))
    assert ei.value.code == INVALID_CONTENT
    assert ei.value.violations


def test_raw_zone_is_immutable():
    with pytest.raises(KernelError) as ei:
        WikiPolicy().validate(_batch("plain raw text\n", path="raw/report.md"))
    assert ei.value.code == INVALID_CONTENT


def test_non_typed_markdown_is_rejected_on_create():
    with pytest.raises(KernelError) as ei:
        WikiPolicy().validate(_batch("no frontmatter, just prose\n"))
    assert ei.value.code == INVALID_CONTENT


def test_delete_skips_validation():
    b = MutationBatch(domain="wiki")
    b.add(Change(op=Op.DELETE, resource_id="w-abc123",
                 before_path="Zettelkasten/a.md"))
    WikiPolicy().validate(b)


def test_policy_identity():
    p = WikiPolicy()
    assert p.domain == "wiki" and p.id_prefix == ID_PREFIX == "w-"
