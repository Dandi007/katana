"""wiki 页面不变量纯函数——L0 机械强制层。

输入：已解析的 frontmatter dict + body str。
输出：错误字符串列表（空列表 = 合规）。
无 IO、无 config、无 server import。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ALLOWED_TYPES: set[str] = {"卡片", "索引", "源码分析", "架构"}
CODE_TYPES: set[str] = {"源码分析", "架构"}

_VALID_SOURCE_TYPES: set[str] = {"human", "mixed", "llm"}
_VALID_CREDIBILITIES: set[str] = {"high", "medium", "low"}

_WIKILINK_RE = re.compile(r"\[\[[^\]]+\]\]")
_REFERENCES_RE = re.compile(r"^# References", re.MULTILINE)


# ---------------------------------------------------------------------------
# check_frontmatter
# ---------------------------------------------------------------------------


def check_frontmatter(fm: dict) -> list[str]:
    """检查 frontmatter 必填字段与值域。"""
    errors: list[str] = []

    # 创建日期
    if "创建日期" not in fm:
        errors.append("缺必填 frontmatter: 创建日期")

    # tags
    if "tags" not in fm or not isinstance(fm.get("tags"), list):
        errors.append("缺必填 frontmatter: tags（须 YAML list）")

    # 类型
    page_type = fm.get("类型")
    if "类型" not in fm:
        errors.append("缺必填 frontmatter: 类型")
    elif page_type not in ALLOWED_TYPES:
        errors.append(f"类型非法: {page_type}（须 ∈ 卡片/索引/源码分析/架构）")

    # source_type / credibility 成对校验
    has_st = "source_type" in fm
    has_cr = "credibility" in fm

    if has_st != has_cr:
        errors.append("source_type 与 credibility 必须成对出现")
    else:
        if has_st:
            st_val = fm["source_type"]
            if st_val not in _VALID_SOURCE_TYPES:
                errors.append(
                    f"source_type 非法: {st_val}（须 ∈ human/mixed/llm）"
                )
        if has_cr:
            cr_val = fm["credibility"]
            if cr_val not in _VALID_CREDIBILITIES:
                errors.append(
                    f"credibility 非法: {cr_val}（须 ∈ high/medium/low）"
                )

    # CODE_TYPES 硬必填 source_type + credibility
    if page_type in CODE_TYPES and not (has_st and has_cr):
        errors.append(f"{page_type} 硬要求 source_type+credibility")

    return errors


# ---------------------------------------------------------------------------
# check_provenance
# ---------------------------------------------------------------------------


def check_provenance(fm: dict, body: str) -> list[str]:
    """检查 provenance：frontmatter sources 或正文 # References 章节。

    CODE_TYPES 硬要求 frontmatter sources，仅 # References 不够。
    """
    errors: list[str] = []

    page_type = fm.get("类型")
    sources = fm.get("sources")
    has_sources = isinstance(sources, list) and len(sources) > 0
    has_references = bool(_REFERENCES_RE.search(body))

    if page_type in CODE_TYPES:
        # CODE_TYPES 硬要求 sources，不论是否有 # References
        if not has_sources:
            errors.append(f"{page_type} 硬要求 frontmatter sources")
    else:
        # 非 CODE_TYPES：sources 或 # References 任一即可（此函数层面宽松）
        if not has_sources and not has_references:
            errors.append("缺 provenance：需 frontmatter sources 或正文 # References")

    return errors


# ---------------------------------------------------------------------------
# check_outlinks
# ---------------------------------------------------------------------------


def check_outlinks(body: str) -> list[str]:
    """检查 body 至少含 1 个 wikilink [[...]]。"""
    if not _WIKILINK_RE.search(body):
        return ["无 outlink（孤岛）：每页 ≥1 [[...]]"]
    return []


# ---------------------------------------------------------------------------
# check_summary
# ---------------------------------------------------------------------------


def check_summary(fm: dict, *, max_len: int = 40) -> list[str]:
    """检查摘要存在且长度 ≤ max_len。"""
    errors: list[str] = []

    if "摘要" not in fm:
        errors.append(f"缺 摘要（一行 ≤{max_len} 字）")
    else:
        summary = fm["摘要"]
        if not isinstance(summary, str) or len(summary) > max_len:
            errors.append(f"摘要超长（>{max_len} 字）")

    return errors


# ---------------------------------------------------------------------------
# validate_page（聚合入口）
# ---------------------------------------------------------------------------


def validate_page(
    fm: dict,
    body: str,
    *,
    require_summary: bool = True,
    require_sources: bool = True,
) -> list[str]:
    """聚合所有检查，不短路，一次报全。

    require_sources=True（默认）：非 CODE_TYPES 页面也要求 frontmatter sources。
    require_sources=False：非 CODE_TYPES 允许仅有 # References。
    CODE_TYPES 不受 require_sources 影响，始终硬要求 sources。
    """
    errors: list[str] = []

    # 1. frontmatter 基本字段
    errors.extend(check_frontmatter(fm))

    # 2. outlinks
    errors.extend(check_outlinks(body))

    # 3. provenance
    page_type = fm.get("类型")
    if page_type in CODE_TYPES or not require_sources:
        # CODE_TYPES：check_provenance 内部硬要求 sources
        # require_sources=False：宽松，允许仅 # References
        errors.extend(check_provenance(fm, body))
    else:
        # require_sources=True & 非 CODE_TYPES：sources 必填
        sources = fm.get("sources")
        has_sources = isinstance(sources, list) and len(sources) > 0
        if not has_sources:
            # 也检查是否完全没有 provenance（sources 缺且无 References）
            has_references = bool(_REFERENCES_RE.search(body))
            if not has_references:
                errors.append("缺 provenance：需 frontmatter sources 或正文 # References")
            else:
                errors.append("缺 provenance：需 frontmatter sources 或正文 # References")

    # 4. 摘要（可选）
    if require_summary:
        errors.extend(check_summary(fm))

    return errors
