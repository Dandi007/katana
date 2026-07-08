import os

from katana_memory_mcp import migrate, store

LEGACY = """---
name: legacy-card
description: 旧卡
status: active
last_verified: 2026-06-25
---

## Fact

原文正文，含任意字段与格式。
"""


def _write(d, fname, text):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        f.write(text)


def test_migrate_inserts_id_only(tmp_path):
    src, dest = str(tmp_path / "src"), str(tmp_path / "dest")
    _write(src, "legacy-card.md", LEGACY)
    r = migrate.migrate([src], dest)
    assert r["migrated"] == 1 and not r["skipped"] and not r["collisions"]
    out = open(os.path.join(dest, "legacy-card.md"), encoding="utf-8").read()
    lines = out.splitlines()
    assert lines[0] == "---" and store.ID_RE.fullmatch(lines[1].removeprefix("id: "))
    # 除插入的 id 行外逐字一致
    assert "\n".join(lines[:1] + lines[2:]) + "\n" == LEGACY


def test_migrate_keeps_existing_id(tmp_path):
    src, dest = str(tmp_path / "src"), str(tmp_path / "dest")
    _write(src, "has-id.md", "---\nid: m-aaaaaa\nname: has-id\ndescription: d\n---\nbody\n")
    r = migrate.migrate([src], dest)
    assert r["migrated"] == 1
    assert "id: m-aaaaaa" in open(os.path.join(dest, "has-id.md")).read()


def test_migrate_skips_unparseable_and_collisions(tmp_path):
    s1, s2, dest = str(tmp_path / "s1"), str(tmp_path / "s2"), str(tmp_path / "dest")
    _write(s1, "bad.md", "no frontmatter")
    _write(s1, "dup.md", LEGACY.replace("legacy-card", "dup"))
    _write(s2, "dup.md", LEGACY.replace("legacy-card", "dup"))
    r = migrate.migrate([s1, s2], dest)
    assert any(p.endswith("bad.md") for p in r["skipped"])
    assert any(p.endswith("dup.md") for p in r["collisions"])
    assert r["migrated"] == 1
