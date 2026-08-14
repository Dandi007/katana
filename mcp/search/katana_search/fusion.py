"""RRF 融合 —— 自 agent-knowledge `service/search.py` 逐条移植。

**刻意移植而不是重写**：这里的常量与加权规则是在真语料上调出来的（短笔记加权、
标题命中加权与注入、原子卡 tiebreak），重造等于把调优成果扔掉再踩一遍。移植时
除去掉外部依赖外不改行为。
"""

from __future__ import annotations

import re
from typing import Any

RRF_K = 60
VECTOR_WEIGHT = 1.0
KEYWORD_WEIGHT = 1.0

# 短笔记加权：原子卡（depth=1）拿 SHORT_NOTE_BOOST/2 ≈ 0.010；
# 深路径的工作记录（depth=5+）只拿 ~0.003。净差约 4-5 个名次，
# 足以把语义强的原子卡顶进前 5。
SHORT_NOTE_BOOST = 0.010

# 标题命中加权：笔记检索最强的信号是 query 几乎等于标题（文件名），而裸
# vector/keyword 对此无感（小模型弱 + 关键词对空格标点脆）。boost 远大于
# RRF(~0.02)，确保标题命中置顶；按覆盖率分级，避免短 query 在长标题上过度加权。
TITLE_EXACT_BOOST = 1.0
TITLE_SUBSTR_BOOST = 0.5


def path_depth(path_str: str) -> int:
    return path_str.count("/")


def _normalize_text(s: str) -> str:
    """去空白与标点（含 CJK 标点），保留 CJK 与字母数字，lowercase。

    对「AI时代的测试」vs「AI 时代的测试…」这类只差空格标点的写法鲁棒。
    """
    return re.sub(r"[\W_]+", "", s, flags=re.UNICODE).lower()


def _title_of(path_str: str) -> str:
    base = path_str.rsplit("/", 1)[-1]
    if base.endswith(".md"):
        base = base[:-3]
    return base


def title_match_boost(query: str, path_str: str) -> float:
    nq = _normalize_text(query)
    if len(nq) < 2:
        return 0.0
    nt = _normalize_text(_title_of(path_str))
    if not nt:
        return 0.0
    if nq == nt:
        return TITLE_EXACT_BOOST
    if nq in nt:
        return TITLE_SUBSTR_BOOST * (len(nq) / len(nt))
    if nt in nq:
        return TITLE_SUBSTR_BOOST * (len(nt) / len(nq))
    return 0.0


def rrf_merge(
    vector_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    k: int = RRF_K,
    query: str = "",
    all_paths: list[str] | None = None,
) -> list[tuple[str, float]]:
    """RRF + 短笔记加权 + 标题加权（含注入）+ 原子卡 tiebreak。

    排序键：分数降序 → keyword_rank 升序（直词 query 优先关键词救回）→
    路径深度升序（原子卡先于深层工作记录）。
    """
    keyword_ranks: dict[str, int] = {}
    for rank, r in enumerate(keyword_results, 1):
        path = str(r.get("path", ""))
        if path not in keyword_ranks:
            keyword_ranks[path] = rank

    scores: dict[str, float] = {}
    for rank, r in enumerate(vector_results, 1):
        path = str(r.get("path", ""))
        scores[path] = scores.get(path, 0.0) + VECTOR_WEIGHT / (k + rank)
    for rank, r in enumerate(keyword_results, 1):
        path = str(r.get("path", ""))
        scores[path] = scores.get(path, 0.0) + KEYWORD_WEIGHT / (k + rank)

    for path in list(scores.keys()):
        scores[path] += SHORT_NOTE_BOOST / (1 + path_depth(path))

    if query:
        for path in list(scores.keys()):
            b = title_match_boost(query, path)
            if b:
                scores[path] += b
        # 注入：标题命中但没进任何一路候选集的文档也要浮现——否则 query≈标题
        # 却整体漏掉的情况会复现（旧实现踩过）。
        for path in all_paths or []:
            if path in scores:
                continue
            b = title_match_boost(query, path)
            if b:
                scores[path] = b

    return sorted(
        scores.items(),
        key=lambda x: (-x[1], keyword_ranks.get(x[0], 999999), path_depth(x[0])),
    )
