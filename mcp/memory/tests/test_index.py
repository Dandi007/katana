import datetime

from katana_memory_mcp import index

TODAY = datetime.date(2026, 8, 10)

CARDS = [
    {"id": "m-000001", "name": "b-card", "description": "B", "status": "active", "type": "reference", "last_verified": "2026-07-01"},
    {"id": "m-000002", "name": "a-card", "description": "A", "status": None, "type": None, "last_verified": None},
    {"id": "m-000003", "name": "dead", "description": "X", "status": "deprecated", "type": None, "last_verified": None},
    {"id": "m-000004", "name": "p-card", "description": "P", "status": "active", "type": "project", "last_verified": None},
    {"id": "m-000005", "name": "u-card", "description": "U", "status": "active", "type": "user", "last_verified": None, "pinned": True},
]


def test_header_leads_and_dead_excluded():
    out = index.render_index(CARDS, "uther", today=TODAY)
    assert out.startswith("<memory-index>") and out.rstrip().endswith("</memory-index>")
    assert "dead" not in out
    # 导航信息必须先于任何卡片行（截断幸存不变量）
    assert out.index("memory_get") < out.index("- [m-")
    assert out.index("memory_index") < out.index("- [m-")
    assert "4 active cards" in out


def test_pinned_always_first_even_with_tiny_budget():
    out = index.render_index(CARDS, "uther", budget_bytes=1, today=TODAY)
    assert "- [m-000005] u-card — U" in out
    # 预算 1 字节：非 pinned 全部省略
    assert "b-card" not in out and "p-card" not in out
    assert "另有 3 张未列出" in out


def test_budget_zero_means_full():
    out = index.render_index(CARDS, "uther", budget_bytes=0, today=TODAY)
    for name in ("a-card", "b-card", "p-card", "u-card"):
        assert name in out
    assert "未列出" not in out


def test_score_type_prior_and_recency_and_hits():
    ref = {"id": "m-1", "type": "reference", "last_verified": None}
    proj = {"id": "m-2", "type": "project", "last_verified": None}
    assert index.score(ref) > index.score(proj)
    fresh = {"id": "m-3", "type": "project", "last_verified": "2026-08-01"}
    assert index.score(fresh, today=TODAY) > index.score(proj, today=TODAY)
    hot = {"id": "m-4", "type": "project", "last_verified": None}
    assert index.score(hot, hits={"m-4": 7}) > index.score(proj, hits={"m-4": 7})


def test_hits_change_selection_order():
    hits = {"m-000004": 31}  # project 卡靠命中反超 reference 卡
    out = index.render_index(CARDS, "uther", hits=hits, today=TODAY)
    assert out.index("p-card") < out.index("b-card")


def test_omission_reported_and_budget_respected():
    many = [{"id": f"m-{i:06x}", "name": f"card-{i:03d}", "description": "d" * 50,
             "status": "active", "type": "project", "last_verified": None}
            for i in range(200)]
    out = index.render_index(many, "uther", budget_bytes=1800, today=TODAY)
    assert len(out.encode("utf-8")) <= 1800
    assert "未列出" in out and "memory_index" in out


def test_hook_payload_shape():
    p = index.hook_payload(CARDS, "uther", today=TODAY)
    assert p["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "<memory-index>" in p["hookSpecificOutput"]["additionalContext"]


def test_hook_payload_empty_when_no_active():
    p = index.hook_payload([CARDS[2]], "uther", today=TODAY)
    assert p["hookSpecificOutput"]["additionalContext"] == ""
