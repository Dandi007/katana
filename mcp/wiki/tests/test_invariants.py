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
    """按上限边界断言，不写死数字。

    上限从 40 放宽到 100：WIKI.md 原文是「≤~40 字」（约数指引），代码却按精确 40
    硬判，导致 667 个有摘要的页里 642 个（96%）被判非法——实测中位 66 字、p90 97。
    是规则数字与真实写作水位脱节，不是数据脏。
    """
    limit = inv.DEFAULT_SUMMARY_MAX_LEN
    fm = _valid_fm(); fm["摘要"] = "字" * (limit + 1)
    assert any("摘要超长" in e for e in inv.check_summary(fm))
    # 恰好等于上限须通过
    fm["摘要"] = "字" * limit
    assert inv.check_summary(fm) == []
    # 实测水位（p90 = 97 字）必须落在合法区间内
    fm["摘要"] = "字" * 97
    assert inv.check_summary(fm) == []


def test_validate_page_aggregates_all_errors():
    fm = {"类型": "随便"}  # 一堆缺失
    errs = inv.validate_page(fm, "正文无 wikilink")
    # 至少同时报：创建日期/tags/类型非法/孤岛/摘要 等多条
    assert len(errs) >= 4


def test_credibility_invalid_value():
    fm = _valid_fm(); fm["source_type"] = "mixed"; fm["credibility"] = "verylow"
    assert any("credibility" in e for e in inv.check_frontmatter(fm))


def test_source_type_invalid_value():
    fm = _valid_fm(); fm["source_type"] = "robot"; fm["credibility"] = "high"
    assert any("source_type" in e for e in inv.check_frontmatter(fm))


def test_strict_sources_required_even_with_references():
    fm = {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "卡片", "摘要": "x"}
    # require_sources=True（默认）：有 # References 但无 sources 仍被拒
    errs = inv.validate_page(fm, "正文 [[p]]\n# References\n- y")
    assert any("frontmatter sources" in e for e in errs)


def test_summary_non_str():
    fm = _valid_fm(); fm["摘要"] = 123
    assert any("摘要" in e for e in inv.check_summary(fm))


def test_code_type_pair_missing_one_no_double_report():
    # 只填 source_type，未填 credibility：只应报"成对"，不应再报 CODE_TYPES"硬要求 source_type+credibility"
    fm = {"创建日期": "2026-06-22 10:00", "tags": ["x"], "类型": "架构", "摘要": "x", "source_type": "mixed", "sources": ["a"]}
    errs = inv.check_frontmatter(fm)
    assert any("成对" in e for e in errs)
    assert not any("硬要求 source_type+credibility" in e for e in errs)
