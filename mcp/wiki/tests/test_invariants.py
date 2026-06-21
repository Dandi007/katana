from katana_wiki_mcp import invariants as inv


def _valid_fm():
    return {
        "创建日期": "2026-06-22 10:00",
        "tags": ["coffee"],
        "类型": "卡片",
        "sources": ["raw/a.md"],
        "摘要": "手冲咖啡的萃取温度与粉水比",
    }


def test_valid_page_no_errors():
    assert inv.validate_page(_valid_fm(), "正文 [[别的页]]") == []


# ---- 负向（头等公民）：每条违规必被捕获 ----
def test_missing_created_date():
    fm = _valid_fm(); del fm["创建日期"]
    assert any("创建日期" in e for e in inv.check_frontmatter(fm))


def test_bad_type():
    fm = _valid_fm(); fm["类型"] = "随便"
    assert any("类型非法" in e for e in inv.check_frontmatter(fm))


def test_tags_not_list():
    fm = _valid_fm(); fm["tags"] = "coffee"
    assert any("tags" in e for e in inv.check_frontmatter(fm))


def test_source_type_without_credibility():
    fm = _valid_fm(); fm["source_type"] = "mixed"
    assert any("成对" in e for e in inv.check_frontmatter(fm))


def test_code_type_requires_source_credibility_and_sources():
    fm = {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "架构", "摘要": "某系统架构"}
    errs_fm = inv.check_frontmatter(fm)
    assert any("source_type+credibility" in e for e in errs_fm)
    errs_prov = inv.check_provenance(fm, "正文 # References\n- x")
    assert any("sources" in e for e in errs_prov)


def test_no_outlink_is_island():
    assert any("孤岛" in e for e in inv.check_outlinks("没有任何 wikilink 的正文"))


def test_outlink_present_ok():
    assert inv.check_outlinks("see [[页A]] and [[页B]]") == []


def test_provenance_via_references_when_not_strict():
    fm = {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片", "摘要": "x"}
    # 宽松：仅 # References 即可
    assert inv.validate_page(fm, "正文 [[p]]\n# References\n- y", require_sources=False) == []


def test_provenance_missing_both():
    fm = {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片", "摘要": "x"}
    assert any("provenance" in e for e in inv.check_provenance(fm, "正文无来源"))


def test_summary_too_long():
    fm = _valid_fm(); fm["摘要"] = "字" * 50
    assert any("摘要超长" in e for e in inv.check_summary(fm))


def test_validate_page_aggregates_all_errors():
    fm = {"类型": "随便"}  # 一堆缺失
    errs = inv.validate_page(fm, "正文无 wikilink")
    # 至少同时报：创建日期/tags/类型非法/孤岛/摘要 等多条
    assert len(errs) >= 4
