import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness import snapshot

def test_delta_created_modified_deleted(tmp_path):
    (tmp_path/"a.md").write_text("a")
    (tmp_path/"b.md").write_text("b")
    before = snapshot.snapshot(tmp_path, snapshot.HARNESS_EXCLUDE)
    (tmp_path/"a.md").write_text("a2")     # modified
    (tmp_path/"c.md").write_text("c")      # created
    (tmp_path/"b.md").unlink()             # deleted
    after = snapshot.snapshot(tmp_path, snapshot.HARNESS_EXCLUDE)
    d = snapshot.delta(before, after)
    assert d["created"] == {"c.md"}
    assert d["modified"] == {"a.md"}
    assert d["deleted"] == {"b.md"}

def test_excludes_harness_paths(tmp_path):
    (tmp_path/"claude-config").mkdir(); (tmp_path/"claude-config/x").write_text("x")
    (tmp_path/"case.log").write_text("log")
    (tmp_path/"real.md").write_text("r")
    snap = snapshot.snapshot(tmp_path, snapshot.HARNESS_EXCLUDE)
    assert "real.md" in snap
    assert not any(k.startswith("claude-config") for k in snap)
    assert "case.log" not in snap
