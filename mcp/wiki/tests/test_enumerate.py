from __future__ import annotations
from pathlib import Path
import katana_wiki_mcp.enumerate as en


def _mk(p: Path, body: str = "正文 [[x]]\n", typ: str = "卡片") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n类型: {typ}\n摘要: s\n---\n{body}", encoding="utf-8")


def test_enumerate_lists_writable_zones_sorted(tmp_path):
    _mk(tmp_path / "Zettelkasten" / "甲.md")
    _mk(tmp_path / "Zettelkasten" / "Index" / "甲索引.md", typ="索引")
    docs = en.enumerate_docs(str(tmp_path))
    paths = [d["path"] for d in docs]
    assert paths == sorted(paths)
    assert "Zettelkasten/甲.md" in paths
    assert "Zettelkasten/Index/甲索引.md" in paths


def test_enumerate_excludes_raw_and_interference_dirs(tmp_path):
    _mk(tmp_path / "Zettelkasten" / "甲.md")
    _mk(tmp_path / "DeepThought" / "x" / "report.md")        # raw zone
    _mk(tmp_path / "转换文档" / "y.md")                       # raw zone
    (tmp_path / ".obsidian").mkdir()
    _mk(tmp_path / ".obsidian" / "z.md")                      # interference
    paths = [d["path"] for d in en.enumerate_docs(str(tmp_path))]
    assert paths == ["Zettelkasten/甲.md"]


def test_enumerate_extracts_type_and_hash_and_mtime(tmp_path):
    _mk(tmp_path / "Zettelkasten" / "甲.md", typ="卡片")
    d = en.enumerate_docs(str(tmp_path))[0]
    assert d["类型"] == "卡片"
    assert isinstance(d["mtime"], float)
    assert len(d["hash"]) == 12
    # 内容变 → hash 变
    h1 = d["hash"]
    (tmp_path / "Zettelkasten" / "甲.md").write_text(
        "---\n类型: 卡片\n摘要: s\n---\n改了\n", encoding="utf-8")
    assert en.enumerate_docs(str(tmp_path))[0]["hash"] != h1


def test_enumerate_tolerates_malformed_frontmatter(tmp_path):
    # 未闭合 [ → yaml.safe_load 抛 ParserError（真机 7 篇同类）
    bad = tmp_path / "Zettelkasten" / "坏yaml.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("---\ntags: [未闭合\n类型: 卡片\n---\n正文 [[x]]\n", encoding="utf-8")
    (tmp_path / "Zettelkasten" / "好.md").write_text(
        "---\n类型: 卡片\n摘要: s\n---\n正文 [[y]]\n", encoding="utf-8")
    docs = en.enumerate_docs(str(tmp_path))           # 不应抛异常
    paths = [d["path"] for d in docs]
    assert "Zettelkasten/坏yaml.md" in paths          # 坏页仍被枚举
    assert "Zettelkasten/好.md" in paths
    bad_doc = next(d for d in docs if d["path"].endswith("坏yaml.md"))
    assert bad_doc["frontmatter"] == {}               # 解析失败退化为 {}
    assert bad_doc["类型"] is None
