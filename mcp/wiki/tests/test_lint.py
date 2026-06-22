from __future__ import annotations
from pathlib import Path
import katana_wiki_mcp.lint as lint


def _page(p: Path, fm: str, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}---\n{body}", encoding="utf-8")


_GOOD_FM = "创建日期: 2026-06-22 10:00\ntags: [t]\n类型: 卡片\nsources: [human:x]\n摘要: s\n"


def test_extract_wikilinks_strips_alias_and_anchor():
    s = lint.extract_wikilinks("见 [[甲|别名]] 和 [[乙#章节]] 与 [[丙]]")
    assert s == {"甲", "乙", "丙"}


def test_orphan_page_reported(tmp_path):
    # 甲、乙 互链；丙 链甲但无人链向丙 → 丙 是 orphan
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "指向 [[乙]]\n")
    _page(tmp_path / "Zettelkasten" / "乙.md", _GOOD_FM, "指向 [[甲]]\n")
    _page(tmp_path / "Zettelkasten" / "丙.md", _GOOD_FM, "无人链向我，但我链 [[甲]]\n")
    codes = {(f["path"], f["code"]) for f in lint.lint_mechanical(str(tmp_path))["findings"]}
    assert ("Zettelkasten/丙.md", "orphan") in codes
    assert ("Zettelkasten/甲.md", "orphan") not in codes  # 乙、丙都链甲


def test_broken_link_reported(tmp_path):
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "指向 [[不存在的页]] 和 [[乙]]\n")
    _page(tmp_path / "Zettelkasten" / "乙.md", _GOOD_FM, "指向 [[甲]]\n")
    fs = lint.lint_mechanical(str(tmp_path))["findings"]
    broken = [f for f in fs if f["code"] == "broken_link"]
    assert any("不存在的页" in f["detail"] for f in broken)


def test_per_page_invariants_reported(tmp_path):
    # 缺 sources / 无 outlink / 缺摘要
    _page(tmp_path / "Zettelkasten" / "坏.md",
          "创建日期: 2026-06-22 10:00\ntags: [t]\n类型: 卡片\n", "无外链孤岛\n")
    codes = {f["code"] for f in lint.lint_mechanical(str(tmp_path))["findings"]
             if f["path"] == "Zettelkasten/坏.md"}
    assert "no_outlink" in codes
    assert "missing_summary" in codes


def test_raw_zone_exempt(tmp_path):
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "链 [[甲]]\n")
    _page(tmp_path / "DeepThought" / "x" / "report.md", "类型: 卡片\n", "raw 内容\n")
    fs = lint.lint_mechanical(str(tmp_path))["findings"]
    assert all("DeepThought" not in f["path"] for f in fs)


def test_extract_wikilinks_strips_path():
    s = lint.extract_wikilinks("见 [[Index/甲综述]] 和 [[乙]]")
    assert s == {"甲综述", "乙"}


def test_path_style_wikilink_not_false_broken_or_orphan(tmp_path):
    # 甲 用路径式链向 Index/乙综述；乙综述 实际存在于 Index/ 子目录
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "见 [[Index/乙综述]]\n")
    _page(tmp_path / "Zettelkasten" / "Index" / "乙综述.md", _GOOD_FM, "回链 [[甲]]\n")
    fs = lint.lint_mechanical(str(tmp_path))["findings"]
    # 不应把 [[Index/乙综述]] 误报为断链
    assert not any(f["code"] == "broken_link" for f in fs)
    # 乙综述 被甲链向，不应误报 orphan
    assert not any(f["code"] == "orphan" and f["path"].endswith("乙综述.md") for f in fs)


def test_lint_tolerates_malformed_frontmatter(tmp_path):
    bad = tmp_path / "Zettelkasten" / "坏yaml.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("---\ntags: [未闭合\n---\n正文链 [[甲]]\n", encoding="utf-8")
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "回链 [[坏yaml]]\n")
    res = lint.lint_mechanical(str(tmp_path))          # 不应抛异常
    assert res["scanned"] >= 2
    # 坏页 body 的 [[甲]] 仍被识别 → 甲 不应被误报 orphan
    assert not any(f["code"] == "orphan" and f["path"].endswith("甲.md")
                   for f in res["findings"])
