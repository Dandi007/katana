from katana_memory_mcp import store

CARD = """---
id: m-3f8a2c
name: sample-card
description: 一行描述
status: active
last_verified: 2026-07-08
metadata:
  type: reference
custom_key: keepme
---

## Fact

body text

## How to Verify

run something
"""


def test_parse_card_extracts_canonical_fields():
    c = store.parse_card(CARD)
    assert c["id"] == "m-3f8a2c"
    assert c["name"] == "sample-card"
    assert c["description"] == "一行描述"
    assert c["status"] == "active"
    assert c["type"] == "reference"
    assert c["last_verified"] == "2026-07-08"
    assert c["extra"] == {"custom_key": "keepme"}
    assert c["body"].startswith("## Fact")


def test_parse_card_no_frontmatter_returns_none():
    assert store.parse_card("just text\n") is None
    assert store.parse_card("") is None


def test_parse_card_missing_optional_fields():
    c = store.parse_card("---\nname: x\ndescription: d\n---\nbody\n")
    assert c["id"] is None and c["status"] is None and c["type"] is None


def test_serialize_roundtrip_canonical_order():
    c = store.parse_card(CARD)
    out = store.serialize_card(c, c["body"])
    c2 = store.parse_card(out)
    for k in ("id", "name", "description", "status", "type", "last_verified", "extra"):
        assert c2[k] == c[k]
    # canonical 键序：id 第一行
    assert out.splitlines()[1].startswith("id: ")


def test_serialize_quotes_risky_scalars():
    meta = {"id": "m-000001", "name": "x", "description": "a: b # c"}
    out = store.serialize_card(meta, "body")
    assert store.parse_card(out)["description"] == "a: b # c"


def test_gen_id_format_and_collision():
    i = store.gen_id(set())
    assert store.ID_RE.fullmatch(i)
    existing = {i}
    assert store.gen_id(existing) not in existing


# ── Fix: 字符类 `!-` 回归测试 ──────────────────────────────────────────────

def test_scalar_risky_leading_chars():
    """行首 `!` 和 `-` 都应被引号包裹；中间的 `-` 不应触发引号。"""
    assert store._scalar("!x").startswith('"')
    assert store._scalar("-lead").startswith('"')
    assert store._scalar("a: b # c").startswith('"')
    # 中间的 `-` 不 risky（正则只对行首 ^ 生效）
    assert store._scalar("plain-word") == "plain-word"


# ── Fix: serialize_card body.rstrip("\n") 尾部换行规约 ─────────────────────

def test_serialize_body_trailing_newlines_roundtrip():
    """body 以多个换行结尾时，roundtrip 后 body 内容不丢，尾部规约为单换行。

    serialize_card 用 body.rstrip("\n") + "\n" 规约尾部，
    parse_card 不剥末尾换行，所以 roundtrip 后 body 以恰好一个 "\n" 结尾。
    """
    meta = {"id": "m-aabbcc", "name": "t", "description": "d", "status": "active"}
    body_with_trailing = "some content\n\n\n"
    out = store.serialize_card(meta, body_with_trailing)
    c = store.parse_card(out)
    # 内容不变（去掉尾部所有换行后相同），且尾部规约为恰好一个换行
    assert c["body"].rstrip("\n") == body_with_trailing.rstrip("\n")
    assert c["body"].endswith("\n") and not c["body"].endswith("\n\n")


# ── Fix: parse_card 结束 fence 边界 ────────────────────────────────────────

def test_parse_card_fence_not_confused_by_partial_fence():
    """frontmatter 内含 `---x` 开头行不被当作结束 fence；body 里含 `---x` 行也不影响。"""
    card_text = (
        "---\n"
        "id: m-112233\n"
        "name: tricky\n"
        "description: '---not a fence'\n"
        "status: active\n"
        "---\n"
        "\n"
        "body line\n"
        "---x not a fence in body\n"
        "more body\n"
    )
    c = store.parse_card(card_text)
    assert c is not None
    assert c["id"] == "m-112233"
    assert c["description"] == "---not a fence"
    assert "---x not a fence in body" in c["body"]


# ── Fix: gen_id 碰撞测试改为确定性 ────────────────────────────────────────

def test_gen_id_deterministic_collision(monkeypatch):
    """用 monkeypatch 替换 secrets.token_hex，前两次返回碰撞值，第三次返回新值。"""
    COLLISION = "aaaaaa"
    NEW_ID = "bbbbbb"
    calls = iter([COLLISION, COLLISION, NEW_ID])
    monkeypatch.setattr(store.secrets, "token_hex", lambda n: next(calls))

    existing = {"m-" + COLLISION}
    result = store.gen_id(existing)
    assert result == "m-" + NEW_ID


# ── Task 2: CRUD operations ──────────────────────────────────────────────────

import os
import pytest


def test_create_writes_file_and_returns_id(tenant_dir):
    c = store.create_card(tenant_dir, "my-card", "d", "body", now="2026-07-08")
    assert store.ID_RE.fullmatch(c["id"])
    assert c["status"] == "active" and c["last_verified"] == "2026-07-08"
    assert os.path.isfile(os.path.join(tenant_dir, "my-card.md"))
    assert c["changed_paths"] == [os.path.join(tenant_dir, "my-card.md")]


def test_create_rejects_duplicate_name(seeded):
    tenant_dir, c1, _ = seeded
    with pytest.raises(ValueError):
        store.create_card(tenant_dir, "card-one", "d", "b")


def test_create_rejects_bad_name_and_type(tenant_dir):
    with pytest.raises(ValueError):
        store.create_card(tenant_dir, "Bad Name!", "d", "b")
    with pytest.raises(ValueError):
        store.create_card(tenant_dir, "ok-name", "d", "b", type="nope")


def test_list_and_get(seeded):
    tenant_dir, c1, c2 = seeded
    listed = store.list_cards(tenant_dir)
    assert {c["id"] for c in listed["cards"]} == {c1["id"], c2["id"]}
    got = store.get_card(tenant_dir, c1["id"])
    assert got["name"] == "card-one" and "## Fact" in got["body"]
    assert store.get_card(tenant_dir, "m-ffffff") is None


def test_list_skips_unparseable_and_id_less(tenant_dir, seeded):
    with open(os.path.join(tenant_dir, "legacy.md"), "w") as f:
        f.write("---\nname: legacy\ndescription: no id yet\n---\nbody\n")
    listed = store.list_cards(tenant_dir)
    assert any(p.endswith("legacy.md") for p in listed["skipped"])


def test_update_fields_and_rename(seeded):
    tenant_dir, c1, _ = seeded
    u = store.update_card(tenant_dir, c1["id"], name="card-renamed", status="stale")
    assert u["name"] == "card-renamed" and u["status"] == "stale"
    assert os.path.isfile(os.path.join(tenant_dir, "card-renamed.md"))
    assert not os.path.exists(os.path.join(tenant_dir, "card-one.md"))
    # id 不变，仍可按 id 找到
    assert store.get_card(tenant_dir, c1["id"])["name"] == "card-renamed"


def test_update_rejects_bad_status(seeded):
    tenant_dir, c1, _ = seeded
    with pytest.raises(ValueError):
        store.update_card(tenant_dir, c1["id"], status="gone")


def test_delete(seeded):
    tenant_dir, c1, _ = seeded
    d = store.delete_card(tenant_dir, c1["id"])
    assert d["id"] == c1["id"]
    assert store.get_card(tenant_dir, c1["id"]) is None
    with pytest.raises(KeyError):
        store.delete_card(tenant_dir, c1["id"])
