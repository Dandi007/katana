"""文档枚举纯函数——L0 确定性层。

无 server import；只读文件系统，返回结构化文档清单。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from katana_wiki_mcp.pages import parse_page

DEFAULT_EXCLUDE_DIRS: set[str] = {
    ".git", ".obsidian", ".wiki", ".trash", "转换文档", "DeepThought",
}


def safe_parse_page(text: str) -> tuple[dict, str]:
    """容错版 parse_page：frontmatter 解析失败时退化为 ({}, 原文)，不抛异常。

    只用于 enumerate/lint 的只读扫描路径；ingest 写入路径仍用严格 pages.parse_page。
    """
    try:
        return parse_page(text)
    except Exception:
        return {}, text


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def enumerate_docs(
    wiki_root: str, *, exclude_dirs: set[str] | None = None
) -> list[dict]:
    """枚举 wiki_root 下所有 .md，prune 干扰/raw 目录，返回结构化清单（按 path 升序）。"""
    excludes = DEFAULT_EXCLUDE_DIRS if exclude_dirs is None else exclude_dirs
    root = Path(wiki_root)
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # 原地剪枝
        dirnames[:] = [d for d in dirnames if d not in excludes]
        for name in filenames:
            if not name.endswith(".md"):
                continue
            fp = Path(dirpath) / name
            text = fp.read_text(encoding="utf-8")
            fm, _ = safe_parse_page(text)
            out.append({
                "path": str(fp.relative_to(root)),
                "类型": fm.get("类型"),
                "frontmatter": fm,
                "mtime": fp.stat().st_mtime,
                "hash": _short_hash(text),
            })
    out.sort(key=lambda d: d["path"])
    return out
