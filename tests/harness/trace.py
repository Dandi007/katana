"""stream-json transcript 的 normalize 与查询。坏行跳过（claude 版本漂移韧性）。"""
import json
from pathlib import Path


def load_trace(path) -> list:
    events = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _tool_uses(events):
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for block in ev.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield block


def tools_used(events) -> list:
    return [b.get("name") for b in _tool_uses(events)]


def skills_loaded(events) -> list:
    out = []
    for b in _tool_uses(events):
        if b.get("name") == "Skill":
            cmd = (b.get("input") or {}).get("skill", "")
            if cmd:
                out.append(cmd)
    return out
