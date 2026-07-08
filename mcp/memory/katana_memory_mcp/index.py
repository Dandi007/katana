"""渲染 SessionStart 注入的 <memory-index> 与 hook JSON payload。"""


def _line(c: dict) -> str:
    return f"- [{c['id']}] {c['name']} — {c['description']}"


def render_index(cards: list[dict], tenant: str) -> str:
    active = [c for c in cards if not c.get("status") or c["status"] == "active"]
    if not active:
        return ""
    untyped = [c for c in active if not c.get("type")]
    types = sorted({c["type"] for c in active if c.get("type")})
    parts = [f"## Memory (tenant: {tenant})"]
    parts += [_line(c) for c in untyped]
    for t in types:
        parts.append(f"### {t}")
        parts += [_line(c) for c in active if c.get("type") == t]
    body = "\n".join(parts)
    footer = (f"Total: {len(active)} active cards · 索引仅 L1 描述；"
              f"全文（事实/证据/验证步骤）用 katana-memory-mcp 的 memory_get(id) 读取")
    return f"<memory-index>\n{body}\n\n{footer}\n</memory-index>"


def hook_payload(cards: list[dict], tenant: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": render_index(cards, tenant),
    }}
