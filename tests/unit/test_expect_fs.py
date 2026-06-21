import sys, pathlib, fnmatch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness.expect_fs import check_fs


def test_created_and_unchanged_outside(tmp_path):
    (tmp_path / "out.md").write_text("hello tokio")
    delta = {"created": {"out.md"}, "modified": set(), "deleted": set()}
    res = check_fs([
        {"created": "out.md"},
        {"content": {"path": "out.md", "matches": "tokio"}},
        {"unchanged_outside": True},
    ], delta, tmp_path, tmp_path)
    assert all(r.ok for r in res), [vars(r) for r in res]


def test_unchanged_outside_fails_on_stray_write(tmp_path):
    delta = {"created": {"out.md", "STRAY.md"}, "modified": set(), "deleted": set()}
    res = check_fs([{"created": "out.md"}, {"unchanged_outside": True}], delta, tmp_path, tmp_path)
    assert not res[1].ok   # STRAY.md 越界


# ──────────────────────────────────────────────────
# Minor B：content 必须 ∈ delta.created ∪ delta.modified
# ──────────────────────────────────────────────────

def test_content_in_delta_created_passes(tmp_path):
    """content 指向 delta.created 内的文件 → PASS。"""
    (tmp_path / "new.md").write_text("hello world")
    delta = {"created": {"new.md"}, "modified": set(), "deleted": set()}
    res = check_fs([
        {"created": "new.md"},
        {"content": {"path": "new.md", "matches": "hello"}},
    ], delta, tmp_path, tmp_path)
    assert all(r.ok for r in res), [vars(r) for r in res]


def test_content_in_delta_modified_passes(tmp_path):
    """content 指向 delta.modified 内的文件 → PASS。"""
    (tmp_path / "log.md").write_text("updated content")
    delta = {"created": set(), "modified": {"log.md"}, "deleted": set()}
    res = check_fs([
        {"modified": "log.md"},
        {"content": {"path": "log.md", "matches": "updated"}},
    ], delta, tmp_path, tmp_path)
    assert all(r.ok for r in res), [vars(r) for r in res]


def test_content_not_in_delta_fails(tmp_path):
    """content 指向 delta 外的预存文件 → FAIL，detail 含 'content path not in delta'。"""
    # 预存文件（fixture 里的，不在 delta 里）
    (tmp_path / "fixture.md").write_text("pre-existing content")
    delta = {"created": {"other.md"}, "modified": set(), "deleted": set()}
    res = check_fs([
        {"created": "other.md"},
        {"content": {"path": "fixture.md", "matches": "pre-existing"}},
    ], delta, tmp_path, tmp_path)
    content_result = next(r for r in res if r.type == "content")
    assert not content_result.ok
    assert "content path not in delta" in content_result.detail
    assert "fixture.md" in content_result.detail
