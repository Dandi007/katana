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
