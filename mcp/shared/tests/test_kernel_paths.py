"""Virtual path confinement tests (design §5.2, §7.2, INV-1)."""
import pytest

from katana_kb_mcp_shared.kernel import paths
from katana_kb_mcp_shared.kernel.errors import INVALID_PATH, KernelError


@pytest.mark.parametrize("good,expected", [
    ("a.md", "a.md"),
    ("Zettelkasten/foo.md", "Zettelkasten/foo.md"),
    ("./a/b.md", "a/b.md"),
    ("a//b.md", "a/b.md"),
    ("笔记/条目.md", "笔记/条目.md"),
])
def test_normalize_accepts_confined(good, expected):
    assert paths.normalize(good) == expected


@pytest.mark.parametrize("bad", [
    "", "/etc/passwd", "../secret", "a/../../b", "a/../b/../..",
    "a\\b", "x\x00y", "..",
])
def test_normalize_rejects_escape(bad):
    with pytest.raises(KernelError) as ei:
        paths.normalize(bad)
    assert ei.value.code == INVALID_PATH


@pytest.mark.parametrize("reserved", [".kb/receipts.json", ".git/config"])
def test_confine_rejects_reserved_namespace(reserved):
    # normalize permits it structurally, but confine (ordinary traffic) denies.
    assert paths.is_reserved(paths.normalize(reserved))
    with pytest.raises(KernelError) as ei:
        paths.confine(reserved)
    assert ei.value.code == INVALID_PATH


def test_confine_allows_ordinary_path():
    assert paths.confine("docs/a.md") == "docs/a.md"
