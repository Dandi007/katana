"""v2 wiki page invariants — pure validation functions.

Input: parsed frontmatter dict + body str.
Output: list of error strings (empty = compliant).
No IO, no config, no server imports.
"""
from __future__ import annotations

import re

ALLOWED_TYPES: set[str] = {"卡片", "索引", "源码分析", "架构"}
CODE_TYPES: set[str] = {"源码分析", "架构"}

_VALID_SOURCE_TYPES: set[str] = {"human", "mixed", "llm"}
_VALID_CREDIBILITIES: set[str] = {"high", "medium", "low"}

_WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")
_REFERENCES_RE = re.compile(r"^# References", re.MULTILINE)

META_FILES = frozenset({"WIKI.md", "log.md"})
DEFAULT_SUMMARY_MAX_LEN = 100


def check_frontmatter(fm: dict) -> list[str]:
    errors: list[str] = []

    if "创建日期" not in fm:
        errors.append("缺必填 frontmatter: 创建日期")

    if "tags" not in fm or not isinstance(fm.get("tags"), list):
        errors.append("缺必填 frontmatter: tags（须 YAML list）")

    page_type = fm.get("类型")
    if "类型" not in fm:
        errors.append("缺必填 frontmatter: 类型")
    elif page_type not in ALLOWED_TYPES:
        errors.append(f"类型非法: {page_type}（须 ∈ 卡片/索引/源码分析/架构）")

    has_st = "source_type" in fm
    has_cr = "credibility" in fm

    if has_st != has_cr:
        errors.append("source_type 与 credibility 必须成对出现")
    else:
        if has_st:
            st_val = fm["source_type"]
            if st_val not in _VALID_SOURCE_TYPES:
                errors.append(f"source_type 非法: {st_val}（须 ∈ human/mixed/llm）")
        if has_cr:
            cr_val = fm["credibility"]
            if cr_val not in _VALID_CREDIBILITIES:
                errors.append(f"credibility 非法: {cr_val}（须 ∈ high/medium/low）")

    if not has_st and not has_cr:
        type_label = page_type if page_type else "页面"
        errors.append(f"{type_label} 硬要求 source_type+credibility")

    return errors


def check_provenance(fm: dict, body: str) -> list[str]:
    errors: list[str] = []

    page_type = fm.get("类型")
    sources = fm.get("sources")
    has_sources = isinstance(sources, list) and len(sources) > 0
    has_references = bool(_REFERENCES_RE.search(body))

    if page_type in CODE_TYPES:
        if not has_sources:
            errors.append(f"{page_type} 硬要求 frontmatter sources")
    else:
        if not has_sources and not has_references:
            errors.append("缺 provenance：需 frontmatter sources 或正文 # References")

    return errors


def check_outlinks(body: str) -> list[str]:
    if not _WIKILINK_RE.search(body):
        return ["无 outlink（孤岛）：每页 ≥1 [[...]]"]
    return []


def check_summary(fm: dict, *, max_len: int = DEFAULT_SUMMARY_MAX_LEN) -> list[str]:
    errors: list[str] = []

    if "摘要" not in fm:
        errors.append(f"缺 摘要（一行 ≤{max_len} 字）")
    else:
        summary = fm["摘要"]
        if not isinstance(summary, str) or len(summary) > max_len:
            errors.append(f"摘要超长（>{max_len} 字）")

    return errors


def validate_page(
    fm: dict,
    body: str,
    *,
    require_summary: bool = True,
    require_sources: bool = True,
) -> list[str]:
    errors: list[str] = []

    errors.extend(check_frontmatter(fm))

    errors.extend(check_outlinks(body))

    page_type = fm.get("类型")
    if page_type in CODE_TYPES or not require_sources:
        errors.extend(check_provenance(fm, body))
    else:
        sources = fm.get("sources")
        has_sources = isinstance(sources, list) and len(sources) > 0
        if not has_sources:
            errors.append("缺 frontmatter sources（ingest 模式要求；# References 不够）")

    if require_summary:
        errors.extend(check_summary(fm))

    return errors


def validate_edit_grade(
    old_fm: dict,
    old_body: str,
    new_fm: dict,
    new_body: str,
) -> list[str]:
    errors: list[str] = []

    if "id" in old_fm and old_fm.get("id") != new_fm.get("id"):
        errors.append("id 不可变")
    if "id" in new_fm and "id" not in old_fm:
        errors.append("edit-grade 不得新增 id")

    for key in ("创建日期", "tags", "类型", "source_type", "credibility"):
        if key in old_fm and key not in new_fm:
            errors.append(f"edit-grade 不得新增缺失: {key}（操作前存在，操作后缺失）")

    if "摘要" in new_fm:
        summary = new_fm["摘要"]
        if not isinstance(summary, str) or len(summary) > DEFAULT_SUMMARY_MAX_LEN:
            errors.append(f"摘要超长（>{DEFAULT_SUMMARY_MAX_LEN} 字）")

    return errors