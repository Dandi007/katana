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


def _write_bytes(d, fname, data: bytes):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, fname), "wb") as f:
        f.write(data)


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


def test_migrate_crlf_body_preserved_byte_exact(tmp_path):
    """body 含 CRLF 行（frontmatter 为 LF）：迁移后 body 字节 100% 原样。"""
    src, dest = str(tmp_path / "src"), str(tmp_path / "dest")
    # frontmatter 用 LF，body 混入 CRLF 行
    raw = (
        b"---\n"
        b"name: crlf-body\n"
        b"description: body has crlf\n"
        b"status: active\n"
        b"last_verified: 2026-07-09\n"
        b"---\n"
        b"\r\n"
        b"## Section\r\n"
        b"\r\n"
        b"\xe4\xb8\xad\xe6\x96\x87\r\n"  # UTF-8 中文 + CRLF
    )
    _write_bytes(src, "crlf-body.md", raw)
    r = migrate.migrate([src], dest)
    assert r["migrated"] == 1
    assert not r["skipped"]
    dest_path = os.path.join(dest, "crlf-body.md")
    with open(dest_path, "rb") as f:
        result = f.read()
    # 结果 = b"---\nid: m-xxxxxx\n" + raw[4:]
    assert result.startswith(b"---\nid: ")
    # raw[4:] 必须字节完全一致
    assert result[result.index(b"\n", 8) + 1:] == raw[4:]


def test_migrate_crlf_frontmatter_goes_to_skipped(tmp_path):
    """frontmatter 本身以 CRLF 开头（---\\r\\n）的无 id 卡：进 skipped，不进 dest。"""
    src, dest = str(tmp_path / "src"), str(tmp_path / "dest")
    raw = (
        b"---\r\n"
        b"name: crlf-fm\r\n"
        b"description: crlf frontmatter\r\n"
        b"status: active\r\n"
        b"last_verified: 2026-07-09\r\n"
        b"---\r\n"
        b"\r\nbody\r\n"
    )
    _write_bytes(src, "crlf-fm.md", raw)
    r = migrate.migrate([src], dest)
    assert any(p.endswith("crlf-fm.md") for p in r["skipped"])
    assert r["migrated"] == 0
    assert not os.path.exists(os.path.join(dest, "crlf-fm.md"))
