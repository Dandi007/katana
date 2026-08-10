"""渲染 SessionStart 注入的 <memory-index> 与 hook JSON payload。

v2（2026-08-10）：预算化 + 优先级渲染，替代全量渲染。

背景：CC harness 对 hook additionalContext 超阈值（~2KB）整体存盘、只把前
~2KB 预览注入 context——全量渲染 500+ 卡时，实际进 context 的是「untyped
优先 + 扫描序」的头部意外子集，且导航 footer 恰好被截掉（PR #64 想修的
问题在 awk→MCP 重写时丢失）。因此本渲染器的不变量：
1. 导航头永远在最前——即使再被截断，幸存部分也说明了怎么查全量；
2. pinned 卡（frontmatter metadata.pinned: true）无条件注入；
3. 其余卡按 score 降序填充至 budget_bytes（0/None = 不限量）；
4. 有省略时，尾行报告未列出数量。
"""

import datetime
import math

# CC harness 的注入预览约 2KB，默认预算留 margin；消费方可用
# /t/<tenant>/index?budget_bytes=N 覆盖，0 = 不限。
DEFAULT_BUDGET_BYTES = 1800

# type 先验：行为约束（user/feedback）> 机制事实（incident/reference）> 项目记录
_TYPE_PRIOR = {"user": 3.0, "feedback": 3.0, "incident": 2.0, "reference": 2.0, "project": 1.0}


def _line(c: dict) -> str:
    return f"- [{c['id']}] {c['name']} — {c['description']}"


def _blen(s: str) -> int:
    return len(s.encode("utf-8"))


def score(c: dict, hits: dict[str, int] | None = None,
          today: datetime.date | None = None) -> float:
    """优先级分 = type 先验 + last_verified 新鲜度 + 命中数（log2 压缩）。"""
    s = _TYPE_PRIOR.get(c.get("type"), 1.0)
    lv = c.get("last_verified")
    if lv and today is not None:
        try:
            days = (today - datetime.date.fromisoformat(lv)).days
        except ValueError:
            days = None
        if days is not None and days >= 0:
            if days <= 30:
                s += 2.0
            elif days <= 90:
                s += 1.0
    if hits:
        s += math.log2(hits.get(c["id"], 0) + 1)
    return s


def render_index(cards: list[dict], tenant: str, *,
                 budget_bytes: int | None = DEFAULT_BUDGET_BYTES,
                 hits: dict[str, int] | None = None,
                 today: datetime.date | None = None) -> str:
    active = [c for c in cards if not c.get("status") or c["status"] == "active"]
    if not active:
        return ""
    pinned = [c for c in active if c.get("pinned")]
    pinned.sort(key=lambda c: c["name"])
    rest = [c for c in active if not c.get("pinned")]
    rest.sort(key=lambda c: (-score(c, hits, today), c["name"]))

    header = (f"<memory-index>\n"
              f"## Memory (tenant: {tenant}) — {len(active)} active cards\n"
              f"本索引为节选（pinned + 高分）；全量清单用 memory_index，"
              f"整卡用 memory_get(id)（katana-memory-mcp）\n")
    footer_close = "\n</memory-index>"

    if not budget_bytes:
        chosen = rest
    else:
        # 预算按 UTF-8 字节数记账；预留尾行（省略提示）的空间。
        used = _blen(header) + _blen(footer_close) + 64
        used += sum(_blen(_line(c)) + 1 for c in pinned)
        chosen = []
        for c in rest:
            n = _blen(_line(c)) + 1
            if used + n > budget_bytes:
                break
            chosen.append(c)
            used += n

    omitted = len(rest) - len(chosen)
    body = "\n".join(_line(c) for c in pinned + chosen)
    tail = f"\n（另有 {omitted} 张未列出：用 memory_index 查全量）" if omitted else ""
    return header + body + tail + footer_close


def hook_payload(cards: list[dict], tenant: str, *,
                 budget_bytes: int | None = DEFAULT_BUDGET_BYTES,
                 hits: dict[str, int] | None = None,
                 today: datetime.date | None = None) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": render_index(
            cards, tenant, budget_bytes=budget_bytes, hits=hits, today=today),
    }}
