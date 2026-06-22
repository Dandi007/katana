from __future__ import annotations
import json
from pathlib import Path
import katana_wiki_mcp.cli as cli


def _seed(tmp_path):
    z = tmp_path / "Zettelkasten"
    z.mkdir()
    (z / "甲.md").write_text(
        "---\n创建日期: 2026-06-22 10:00\ntags: [t]\n类型: 卡片\nsources: [human:x]\n摘要: s\n"
        "---\n链 [[乙]]\n", encoding="utf-8")
    (z / "乙.md").write_text(
        "---\n创建日期: 2026-06-22 10:00\ntags: [t]\n类型: 卡片\nsources: [human:x]\n摘要: s\n"
        "---\n链 [[甲]]\n", encoding="utf-8")


def test_cli_list_docs_json(tmp_path, capsys, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(cli, "_resolve_wiki_root", lambda: str(tmp_path))
    rc = cli.main(["list-docs"])
    assert rc == 0
    docs = json.loads(capsys.readouterr().out)
    assert {d["path"] for d in docs} == {"Zettelkasten/甲.md", "Zettelkasten/乙.md"}


def test_cli_lint_mechanical_json(tmp_path, capsys, monkeypatch):
    _seed(tmp_path)
    (tmp_path / "Zettelkasten" / "甲.md").write_text(
        "---\n创建日期: 2026-06-22 10:00\ntags: [t]\n类型: 卡片\nsources: [human:x]\n摘要: s\n"
        "---\n链 [[黑洞]]\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_resolve_wiki_root", lambda: str(tmp_path))
    rc = cli.main(["lint-mechanical"])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert any(f["code"] == "broken_link" for f in res["findings"])


def test_cli_lint_mechanical_zone_flag(tmp_path, capsys, monkeypatch):
    _seed(tmp_path)
    (tmp_path / "智元工作").mkdir(parents=True, exist_ok=True)
    (tmp_path / "智元工作" / "日报.md").write_text("# 日报\n无 frontmatter\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_resolve_wiki_root", lambda: str(tmp_path))
    rc = cli.main(["lint-mechanical", "--zone", "Zettelkasten"])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert all("智元工作" not in f["path"] for f in res["findings"])
