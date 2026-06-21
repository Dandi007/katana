"""轴① 过程断言。消费 trace.load_trace/tools_used/skills_loaded。

断言类型（无 trace_ 前缀）：
  skill_loaded  — Skill 事件的 input.skill == 指定值
  tool_used     — 指定工具被调用过
  tool_absent   — 指定工具未被调用
  tool_count    — 指定工具调用次数（eq/min/max 组合）
  sequence      — 松子序列（工具调用顺序含指定子序列）
"""
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    type: str
    ok: bool
    detail: str = ""


def check_process(asserts: list, trace_path) -> list[Result]:
    """对 trace_path 执行所有过程断言，返回 Result 列表。

    trace_path 缺失或文件不存在时，每条断言均为 False + "no trace captured"。
    """
    from . import trace as tracemod

    # 尝试加载 trace；若不可用则所有断言失败
    trace_missing = False
    events = []
    if not trace_path or not Path(trace_path).exists():
        trace_missing = True
    else:
        events = tracemod.load_trace(trace_path)

    out = []
    for a in asserts:
        ((typ, val),) = a.items()
        if trace_missing:
            out.append(Result(typ, False, "no trace captured"))
            continue
        try:
            out.append(_check(typ, val, events, tracemod))
        except Exception as e:
            out.append(Result(typ, False, f"assert error: {e}"))
    return out


def _check(typ: str, val, events: list, tracemod) -> Result:
    if typ == "skill_loaded":
        ok = val in tracemod.skills_loaded(events)
        return Result(typ, ok, "" if ok else f"skill {val!r} not loaded")

    if typ == "tool_used":
        ok = val in tracemod.tools_used(events)
        return Result(typ, ok, "" if ok else f"tool {val!r} not used")

    if typ == "tool_absent":
        ok = val not in tracemod.tools_used(events)
        return Result(typ, ok, "" if ok else f"tool {val!r} unexpectedly used")

    if typ == "tool_count":
        n = tracemod.tools_used(events).count(val["tool"])
        ok = (("eq" not in val or n == val["eq"]) and
              ("min" not in val or n >= val["min"]) and
              ("max" not in val or n <= val["max"]))
        return Result(typ, ok, "" if ok else f"{val['tool']} count={n}")

    if typ == "sequence":
        seq = tracemod.tools_used(events)
        i = 0
        for want in val:
            while i < len(seq) and seq[i] != want:
                i += 1
            if i >= len(seq):
                return Result(typ, False, f"sequence broke at {want!r}")
            i += 1
        return Result(typ, True, "")

    raise ValueError(f"unknown process assert type: {typ!r}")
