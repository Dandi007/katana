from katana_memory_mcp import index

CARDS = [
    {"id": "m-000001", "name": "b-card", "description": "B", "status": "active", "type": "reference", "last_verified": "2026-07-01"},
    {"id": "m-000002", "name": "a-card", "description": "A", "status": None, "type": None, "last_verified": None},
    {"id": "m-000003", "name": "dead", "description": "X", "status": "deprecated", "type": None, "last_verified": None},
    {"id": "m-000004", "name": "p-card", "description": "P", "status": "active", "type": "project", "last_verified": None},
]


def test_render_filters_active_and_groups_by_type():
    out = index.render_index(CARDS, "uther")
    assert out.startswith("<memory-index>") and out.rstrip().endswith("</memory-index>")
    assert "- [m-000002] a-card — A" in out
    assert "- [m-000001] b-card — B" in out
    assert "dead" not in out
    assert out.index("### project") < out.index("### reference")
    # untyped 在任何 type 组之前
    assert out.index("a-card") < out.index("### project")
    assert "Total: 3 active cards" in out
    assert "memory_get" in out


def test_hook_payload_shape():
    p = index.hook_payload(CARDS, "uther")
    assert p["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "<memory-index>" in p["hookSpecificOutput"]["additionalContext"]


def test_hook_payload_empty_when_no_active():
    p = index.hook_payload([CARDS[2]], "uther")
    assert p["hookSpecificOutput"]["additionalContext"] == ""
