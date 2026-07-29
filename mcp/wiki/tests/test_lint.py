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


def test_extract_wikilinks_preserves_path_form():
    """路径形式必须保留原样——提取阶段剥掉前缀会让同名页互相顶替。

    路径→页面的归一交给 _link_keys（登记全部路径后缀），不在提取阶段做。
    """
    s = lint.extract_wikilinks("见 [[Index/甲综述]] 和 [[乙]]")
    assert s == {"Index/甲综述", "乙"}


def test_same_basename_pages_do_not_shadow_each_other(tmp_path):
    """不同目录下的同名页：只链其中一个，另一个仍须报 orphan。

    旧实现按 basename 字符串比对，[[Index/甲]] 会让顶层 甲.md 也算「被链」。
    """
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "顶层甲 [[乙]]\n")
    _page(tmp_path / "Zettelkasten" / "Index" / "甲.md", _GOOD_FM, "索引甲 [[乙]]\n")
    _page(tmp_path / "Zettelkasten" / "乙.md", _GOOD_FM, "只链索引那个 [[Index/甲]]\n")
    fs = lint.lint_mechanical(str(tmp_path))["findings"]
    orphans = {f["path"] for f in fs if f["code"] == "orphan"}
    assert "Zettelkasten/甲.md" in orphans            # 顶层甲无人链
    assert "Zettelkasten/Index/甲.md" not in orphans  # 索引甲被链到


def test_ambiguous_link_is_flagged(tmp_path):
    """裸 [[甲]] 同时命中多页时报 ambiguous_link，而非静默挑一个。"""
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "顶层甲 [[乙]]\n")
    _page(tmp_path / "Zettelkasten" / "Index" / "甲.md", _GOOD_FM, "索引甲 [[乙]]\n")
    _page(tmp_path / "Zettelkasten" / "乙.md", _GOOD_FM, "歧义引用 [[甲]]\n")
    fs = lint.lint_mechanical(str(tmp_path))["findings"]
    amb = [f for f in fs if f["code"] == "ambiguous_link"]
    assert len(amb) == 1 and "甲" in amb[0]["detail"]


def test_control_files_are_not_linted(tmp_path):
    """CLAUDE.md / AGENTS.md / .audit/ / checkpoints/ 是 agent 与工具产物，非知识页。"""
    _page(tmp_path / "Zettelkasten" / "真页.md", _GOOD_FM, "正文 [[真页]]\n")
    (tmp_path / "Zettelkasten" / "CLAUDE.md").write_text("# 指令\n", encoding="utf-8")
    (tmp_path / "Zettelkasten" / "AGENTS.md").write_text("# 指令\n", encoding="utf-8")
    (tmp_path / "Zettelkasten" / ".audit").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Zettelkasten" / ".audit" / "r.md").write_text("裸报告\n", encoding="utf-8")
    (tmp_path / "Zettelkasten" / "checkpoints" / "x").mkdir(parents=True, exist_ok=True)
    (tmp_path / "Zettelkasten" / "checkpoints" / "x" / "progress.md").write_text(
        "# Progress\n", encoding="utf-8")
    touched = {f["path"] for f in lint.lint_mechanical(str(tmp_path))["findings"]}
    assert not any(p.endswith(("CLAUDE.md", "AGENTS.md")) for p in touched)
    assert not any("/.audit/" in p or "checkpoints/" in p for p in touched)


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


def test_lint_mechanical_zone_scoping(tmp_path):
    # wiki zone 内一页（缺各种东西）+ zone 外一页（工作记录，无 frontmatter）
    _page(tmp_path / "Zettelkasten" / "甲.md", _GOOD_FM, "正文 [[乙]]\n")
    (tmp_path / "智元工作").mkdir(parents=True, exist_ok=True)
    (tmp_path / "智元工作" / "周报.md").write_text("# 周报\n无 frontmatter 的工作记录\n", encoding="utf-8")
    # 不限 zone：两边都扫到
    full = lint.lint_mechanical(str(tmp_path))
    assert any("智元工作" in f["path"] for f in full["findings"])
    # 限 zone=Zettelkasten：只在 wiki 子树内，工作记录零 finding
    scoped = lint.lint_mechanical(str(tmp_path), zone="Zettelkasten")
    assert all("智元工作" not in f["path"] for f in scoped["findings"])
    assert all(f["path"].startswith("Zettelkasten/") for f in scoped["findings"])


def test_excluded_zone_reports_not_checked(tmp_path):
    """raw/排除区必须说明「未做检查」，不能静默返回 findings:[] 假装干净。"""
    (tmp_path / "DeepThought").mkdir()
    (tmp_path / "DeepThought" / "a.md").write_text("no frontmatter\n", encoding="utf-8")
    r = lint.lint_mechanical(str(tmp_path), zone="DeepThought")
    assert r["scanned"] == 0
    assert r["findings"] == []
    assert r["skipped"] and "未做任何检查" in r["skipped"][0]


def test_findings_pagination_and_summary(tmp_path):
    """findings 必须可分页，且始终带全量汇总（全库一次全取约 80k tokens，不可用）。"""
    zone = tmp_path / "Zettelkasten"
    zone.mkdir()
    for i in range(5):
        (zone / f"p{i}.md").write_text("bare body, no frontmatter\n", encoding="utf-8")
    full = lint.lint_mechanical(str(tmp_path), zone="Zettelkasten", limit=None)
    assert full["total_findings"] == len(full["findings"]) > 5
    assert full["truncated"] is False
    assert sum(full["by_code"].values()) == full["total_findings"]

    page = lint.lint_mechanical(str(tmp_path), zone="Zettelkasten", offset=0, limit=3)
    assert len(page["findings"]) == 3
    assert page["total_findings"] == full["total_findings"]  # 汇总不受分页影响
    assert page["truncated"] is True
    assert page["by_code"] == full["by_code"]
