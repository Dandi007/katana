"""wiki_query fat tool protocol — migrated from v1 query.py.

SYNTHESIS_CONTRACT / READ_LADDER: protocol constants embedded in tool responses.
_do_query: injectable dependencies (search_fn/log_fn/now_fn), no side effects, testable.
"""
from __future__ import annotations

SYNTHESIS_CONTRACT: str = (
    "综合协议（必须遵守）："
    "① 每条 claim 必须携带对应页面的 wikilink / source citation；"
    "② 跨页桥接推理（wiki 未直接支撑的连接）必须显式标注 [inference]；"
    "③ 既无 citation 又无 [inference] 标注的句子不得出现在 wiki 溯源答案中；"
    "④ cold=True 时 wiki 不覆盖此问题，禁止用裸参数化知识包装成 wiki 答案——"
    "  须明示 non-wiki 来源，或改为通用知识模式并显式说明。"
)

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


def _do_query(
    question: str,
    top_k: int = 10,
    *,
    search_fn,
    log_fn,
    now_fn,
) -> dict:
    resp = search_fn(question, top_k=top_k)
    results = resp.get("results", [])
    index_health = resp.get("index_health", {})

    if not results:
        ts = now_fn()
        log_fn(f"## [{ts}] query | gap: {question}")
        return {
            "cold": True,
            "message": (
                "wiki 不覆盖此问题；勿用裸参数化知识冒充 wiki 答案。"
                "可改为 web/通用知识模式，但须显式标 non-wiki。"
            ),
            "candidates": [],
            "synthesis_contract": SYNTHESIS_CONTRACT,
            "index_health": index_health,
        }

    candidates = [
        {"id": r.get("id", ""), "score": r.get("score", 0), "title": r.get("title", ""), "snippet": r.get("snippet", "")}
        for r in results
    ]
    return {
        "cold": False,
        "candidates": candidates,
        "candidate_count": len(candidates),
        "synthesis_contract": SYNTHESIS_CONTRACT,
        "support_gate": SUPPORT_GATE,
        "read_ladder": READ_LADDER,
        "index_health": index_health,
    }