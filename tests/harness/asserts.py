"""Mechanical assertion primitives. file_exists/file_absent are glob-aware."""
from dataclasses import dataclass
from pathlib import Path
import glob as globmod
import json
import re
import subprocess


@dataclass
class Ctx:
    """Assertion execution context."""
    cwd: Path  # case 的 kb 工作目录
    stdout: str
    case_log: Path
    contract_dir: Path  # 契约文件所在目录（解析 script 相对路径）
    trace_path: Path = None  # case.trace.jsonl 路径；None 表示未抓到 trace


@dataclass
class AssertResult:
    """Result of a single assertion check."""
    type: str
    ok: bool
    detail: str = ""


def _expand(s: str, ctx: Ctx) -> str:
    """Expand placeholders in assertion strings."""
    return s.replace("{cwd}", str(ctx.cwd)).replace("{case_log}", str(ctx.case_log))


def _json_get(obj, dotted: str):
    """Navigate JSON object using dotted path notation (e.g., '$.a.b')."""
    cur = obj
    for part in dotted.removeprefix("$.").split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def run_asserts(asserts: list, ctx: Ctx) -> list[AssertResult]:
    """Execute all assertions and return structured results."""
    out = []
    for a in asserts:
        ((typ, val),) = a.items()
        try:
            out.append(_check(typ, val, ctx))
        except Exception as e:  # 断言执行异常 = 失败而非崩 runner
            out.append(AssertResult(typ, False, f"assert error: {e}"))
    return out


def _check(typ, val, ctx) -> AssertResult:
    """Execute a single assertion check."""
    if typ == "file_exists":
        pat = _expand(val, ctx)
        matches = bool(globmod.glob(pat))
        return AssertResult(typ, matches, f"no match: {pat}" if not matches else "")

    if typ == "file_absent":
        pat = _expand(val, ctx)
        hits = globmod.glob(pat)
        return AssertResult(typ, not hits, f"unexpected: {hits[:3]}" if hits else "")

    if typ == "file_grep":
        p = Path(_expand(val["path"], ctx))
        if not p.exists():
            return AssertResult(typ, False, f"file missing: {p}")
        ok = re.search(val["pattern"], p.read_text(encoding="utf-8"), re.M) is not None
        return AssertResult(
            typ, ok, f"pattern '{val['pattern']}' not in {p.name}" if not ok else ""
        )

    if typ == "stdout_grep":
        ok = re.search(val, ctx.stdout, re.M) is not None
        return AssertResult(typ, ok, f"pattern '{val}' not in stdout" if not ok else "")

    if typ == "size_min":
        p = Path(_expand(val["path"], ctx))
        ok = p.exists() and p.stat().st_size >= int(val["bytes"])
        return AssertResult(
            typ, ok, f"{p.name} < {val['bytes']}B" if not ok else ""
        )

    if typ == "json_path":
        p = Path(_expand(val["file"], ctx))
        got = _json_get(json.loads(p.read_text(encoding="utf-8")), val["path"])
        ok = got == val["equals"]
        return AssertResult(typ, ok, f"{val['path']}={got!r}" if not ok else "")

    if typ.startswith("trace_"):
        from . import trace as tracemod
        if not ctx.trace_path or not Path(ctx.trace_path).exists():
            return AssertResult(typ, False, "no trace captured")
        events = tracemod.load_trace(ctx.trace_path)
        if typ == "trace_skill_loaded":
            ok = val in tracemod.skills_loaded(events)
            return AssertResult(typ, ok, "" if ok else f"skill {val!r} not loaded")
        if typ == "trace_tool_used":
            ok = val in tracemod.tools_used(events)
            return AssertResult(typ, ok, "" if ok else f"tool {val!r} not used")
        if typ == "trace_tool_absent":
            ok = val not in tracemod.tools_used(events)
            return AssertResult(typ, ok, "" if ok else f"tool {val!r} unexpectedly used")
        if typ == "trace_tool_count":
            n = tracemod.tools_used(events).count(val["tool"])
            ok = (("eq" not in val or n == val["eq"]) and
                  ("min" not in val or n >= val["min"]) and
                  ("max" not in val or n <= val["max"]))
            return AssertResult(typ, ok, "" if ok else f"{val['tool']} count={n}")
        if typ == "trace_sequence":
            seq = tracemod.tools_used(events); i = 0
            for want in val:
                while i < len(seq) and seq[i] != want:
                    i += 1
                if i >= len(seq):
                    return AssertResult(typ, False, f"sequence broke at {want!r}")
                i += 1
            return AssertResult(typ, True, "")
        if typ == "trace_grep":
            raw = Path(ctx.trace_path).read_text(encoding="utf-8")
            ok = re.search(val, raw, re.M) is not None
            return AssertResult(typ, ok, "" if ok else f"pattern {val!r} not in trace")

    if typ == "script":
        script = (ctx.contract_dir / val).resolve()
        if not script.is_relative_to(ctx.contract_dir.resolve()):
            raise ValueError(f"script path escapes contract_dir: {val!r}")
        env = {
            "KB_DIR": str(ctx.cwd),
            "CASE_LOG": str(ctx.case_log),
            "CASE_DIR": str(ctx.cwd.parent),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        }
        r = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = r.returncode == 0
        detail = (r.stdout + r.stderr)[-500:] if not ok else ""
        return AssertResult(typ, ok, detail)

    raise ValueError(typ)
