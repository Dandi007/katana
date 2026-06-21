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
