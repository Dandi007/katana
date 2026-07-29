"""wiki_query fat tool 的业务逻辑层。

SYNTHESIS_CONTRACT / READ_LADDER：综合协议常量，嵌入 tool 返回值下发给模型。
_do_query：注入式依赖（search_fn/log_fn/now_fn），无副作用，便于单测。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 协议常量（摘自 query skill §3/§4 要点）
# ---------------------------------------------------------------------------

SYNTHESIS_CONTRACT: str = (
    "综合协议（必须遵守）："
    "① 每条 claim 必须携带对应页面的 wikilink / source citation；"
    "② 跨页桥接推理（wiki 未直接支撑的连接）必须显式标注 [inference]；"
    "③ 既无 citation 又无 [inference] 标注的句子不得出现在 wiki 溯源答案中；"
    "④ cold=True 时 wiki 不覆盖此问题，禁止用裸参数化知识包装成 wiki 答案——"
    "  须明示 non-wiki 来源，或改为通用知识模式并显式说明。"
)

# cold 只在结果集为空时为 True，而检索分数无法区分「真命中」与「噪声」：
# 实测标题精确命中 top1≈1.03，但自然语言问已覆盖话题只有 0.0211~0.0378，
# 与无覆盖话题的噪声区间 0.0206~0.0341 完全重叠。任何单一阈值都会误杀
# 「问得像人话」的真命中。因此支撑性判断由模型逐条自评，服务端只强制它必须做。
SUPPORT_GATE: str = (
    "⚠️ 支撑性自检（在综合前必须先做，不可跳过）："
    "① cold=False 只表示「检索有返回」，不表示「wiki 覆盖此问题」——"
    "  分数不可作为相关性判据（标题字面匹配会拿到高分，而自然语言提问命中真页面时分数"
    "  与无关页噪声同量级），故不得用 score 高低替代阅读判断；"
    "② 逐个候选判断它是否真的支撑本问题（snippet 不足以判断时按 read_ladder 读全文）；"
    "③ 若没有任何候选支撑该问题，等同 cold：必须显式声明 wiki 未覆盖，"
    "  并按 non-wiki 来源作答或改通用知识模式，禁止拿低分候选凑答案；"
    "  同时调 wiki_report_gap(question) 记一条 gap log，让盲区可见；"
    "④ 在答案开头用一句话交代自检结论（例如「wiki 有 N 篇相关」或「wiki 未覆盖，以下为 non-wiki」）。"
)

READ_LADDER: str = (
    "候选阅读梯（硬阈值）："
    "候选 ≤5 → 全部内联阅读，不跳过；"
    "候选 >5 → 派 Explore subagent（给问题+全候选列表，要求带路径返回相关段落），"
    "  无 subagent 工具时内联读 top-5 并在答案中声明覆盖限制。"
    "阈值由规则决定，不由判断决定。"
)

# ---------------------------------------------------------------------------
# 核心逻辑（注入式依赖）
# ---------------------------------------------------------------------------

def _do_query(
    question: str,
    scope: str | None,
    wiki_root: str,
    top_k: int = 10,
    *,
    search_fn,
    log_fn,
    now_fn,
) -> dict:
    """判重检索 + cold/hot 分支。

    Args:
        question: 提问文本。
        scope:    检索目录（相对 kb_root），None 表示整库。
        wiki_root: wiki 根绝对路径（cold 时写 gap log 用）。
        top_k:    候选上限。
        search_fn: `(q, *, top_k, dir, ...) -> SearchResponse`（注入，便于单测）。
        log_fn:   `(wiki_root, line) -> None`（注入，便于单测）。
        now_fn:   `() -> str` 时间戳（注入，便于单测）。

    Returns:
        hot: {"cold": False, "candidates": [...], "candidate_count": N,
              "synthesis_contract": SYNTHESIS_CONTRACT, "support_gate": SUPPORT_GATE,
              "read_ladder": READ_LADDER}
        cold: {"cold": True, "message": ..., "candidates": [],
               "synthesis_contract": SYNTHESIS_CONTRACT}
    """
    resp = search_fn(question, top_k=top_k, dir=scope)
    results = resp.results

    if not results:
        # cold 路径：记 gap log，提示禁止冒充
        ts = now_fn()
        log_fn(wiki_root, f"## [{ts}] query | gap: {question}")
        return {
            "cold": True,
            "message": (
                "wiki 不覆盖此问题；勿用裸参数化知识冒充 wiki 答案。"
                "可改为 web/通用知识模式，但须显式标 non-wiki。"
            ),
            "candidates": [],
            "synthesis_contract": SYNTHESIS_CONTRACT,
        }

    # hot 路径：返回候选 + 协议
    candidates = [
        {"path": r.path, "score": r.score, "title": r.title, "snippet": r.snippet}
        for r in results
    ]
    return {
        "cold": False,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "synthesis_contract": SYNTHESIS_CONTRACT,
        "support_gate": SUPPORT_GATE,
        "read_ladder": READ_LADDER,
    }
