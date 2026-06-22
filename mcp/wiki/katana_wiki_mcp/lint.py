"""机械 lint 纯函数——确定性体检层（跨页 + 逐页复用 invariants）。

无 server import；只读文件系统。findings 为结构化 dict 列表。
"""
from __future__ import annotations

import re

from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp.enumerate import enumerate_docs, safe_parse_page
from pathlib import Path

_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

# invariants 文案 → 机械 code 的映射（substr 匹配）
_CODE_MAP = [
    ("无 outlink", "no_outlink"),
    ("缺 provenance", "missing_provenance"),
    ("缺 frontmatter sources", "missing_provenance"),
    ("硬要求 frontmatter sources", "missing_provenance"),
    ("缺 摘要", "missing_summary"),
    ("摘要超长", "missing_summary"),
    ("缺必填 frontmatter", "missing_frontmatter"),
    ("类型非法", "missing_frontmatter"),
    ("source_type", "missing_frontmatter"),
    ("credibility", "missing_frontmatter"),
]


def extract_wikilinks(body: str) -> set[str]:
    """提取 body 中所有 [[target]]，剥离 |alias、#anchor 与路径前缀。"""
    out: set[str] = set()
    for m in _WIKILINK_RE.findall(body):
        t = m.split("|")[0].split("#")[0].strip()
        t = t.rsplit("/", 1)[-1]  # [[Index/甲]] → 甲，合 Obsidian basename 消歧语义
        if t:
            out.add(t)
    return out


def _map_code(msg: str) -> str:
    for sub, code in _CODE_MAP:
        if sub in msg:
            return code
    return "other"


def _basename(path: str) -> str:
    return Path(path).name[:-3]  # 去 .md


def lint_mechanical(
    wiki_root: str, path: str | None = None, *,
    zone: str | None = None, exclude_dirs: set[str] | None = None
) -> dict:
    """机械体检：逐页 invariants + 跨页 orphan/broken_link。raw zone 由枚举层豁免。
    Args: path 可选，限定单页逐页检查（跨页基线仍扫全 zone）；zone 可选，限定子目录前缀（如 "Zettelkasten"），跨页基线只在该 zone 内算。"""
    docs = enumerate_docs(wiki_root, exclude_dirs=exclude_dirs)
    if zone:
        z = zone.rstrip("/") + "/"
        docs = [d for d in docs if d["path"].startswith(z)]
    findings: list[dict] = []

    # 跨页基线：所有页 basename 集合 + 所有被链 target 集合
    all_basenames = {_basename(d["path"]) for d in docs}
    linked_targets: set[str] = set()
    bodies: dict[str, str] = {}
    for d in docs:
        text = (Path(wiki_root) / d["path"]).read_text(encoding="utf-8")
        _, body = safe_parse_page(text)
        bodies[d["path"]] = body
        linked_targets |= extract_wikilinks(body)

    targets = [path] if path else [d["path"] for d in docs]
    for d in docs:
        if d["path"] not in targets:
            continue
        body = bodies[d["path"]]
        fm = d["frontmatter"]

        # 逐页 invariants（非 CODE_TYPES 宽松：允许 # References 充当 provenance）
        for msg in _inv.validate_page(fm, body, require_summary=True, require_sources=False):
            findings.append({"path": d["path"], "code": _map_code(msg), "detail": msg})

        # 跨页：orphan（无人链向本页 basename）
        if _basename(d["path"]) not in linked_targets:
            findings.append({"path": d["path"], "code": "orphan",
                             "detail": "无任何 wikilink 指向本页"})

        # 跨页：broken_link（本页链向的 target 无对应文件）
        for tgt in extract_wikilinks(body):
            if tgt not in all_basenames:
                findings.append({"path": d["path"], "code": "broken_link",
                                 "detail": f"断链 [[{tgt}]]：无对应页面"})

    return {"findings": findings, "skipped": [], "scanned": len(targets)}
