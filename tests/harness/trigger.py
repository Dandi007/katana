"""claude -p 调用封装（v2）。单轮 / 多轮（--resume）统一入口 run()。

已知坑只解一次：
- prompt 走 stdin（--add-dir 等变长参数会吞位置参数 prompt）
- stdout(stream-json 事件流) 写 log_dir/case.trace.jsonl
- 超时杀整个进程组（start_new_session + killpg）
- 模型一律显式 --model 传入（G5：不靠 setter 决定模型）
"""
from dataclasses import dataclass
from pathlib import Path
import json, os, signal, subprocess


class ClaudeTimeout(Exception):
    pass


@dataclass
class Result:
    result_text: str
    trace_path: str
    exit_code: int


# ──────────────────────────────────────────────────
# 内部工具函数
# ──────────────────────────────────────────────────

def _result_text(stream_stdout: str) -> str:
    """从 stream-json 输出取末条 type==result 事件的 result 字段。
    坏行/空行跳过（韧性）。找不到 result 事件则原样返回整段输出。"""
    result_text = None
    for line in stream_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "result":
            result_text = ev.get("result", "") or ""
    return result_text if result_text is not None else stream_stdout


def _parse_session(stream_stdout: str):
    """从 stream-json 输出取 (session_id, result_text)。
    取首条含 session_id 的事件；末条 result 事件的 result 为文本。"""
    session_id = None
    result_text = None
    for line in stream_stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session_id is None and ev.get("session_id"):
            session_id = ev["session_id"]
        if ev.get("type") == "result":
            result_text = ev.get("result", "") or ""
    if result_text is not None:
        return session_id, result_text
    return session_id, stream_stdout


def _build_cmd(binary: str, model: str, tools: list) -> list:
    cmd = [binary, "-p",
           "--model", model,
           "--output-format", "stream-json",
           "--verbose"]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    return cmd


def _popen(cmd, cwd, env) -> subprocess.Popen:
    full_env = {**os.environ, **env}
    return subprocess.Popen(
        cmd, cwd=str(cwd), env=full_env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True,
        start_new_session=True,
    )


def _communicate(proc, prompt: str, timeout: int, label: str) -> str:
    try:
        out, _ = proc.communicate(input=prompt, timeout=timeout)
        return out
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
        if proc.stdout:
            proc.stdout.close()
        if proc.stdin:
            proc.stdin.close()
        raise ClaudeTimeout(f"timeout {timeout}s{label}")


# ──────────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────────

def run(
    *,
    prompt: str | None = None,
    turns: list | None = None,
    cwd,
    log_dir,
    model: str,
    tools: list,
    timeout: int,
    env: dict,
    claude_bin: str | None = None,
) -> Result:
    """统一入口。

    单轮：传 prompt（str）。
    多轮：传 turns（list[str]），内部用 --resume 续 session。
    两者互斥；两者皆传 raise ValueError。

    Returns Result(result_text, trace_path, exit_code)。
    trace 写 log_dir/case.trace.jsonl。
    超时抛 ClaudeTimeout。
    """
    if (prompt is None) == (turns is None):
        raise ValueError("run(): 必须且只能传 prompt 或 turns 其中一个")

    binary = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    canonical_trace = log_dir / "case.trace.jsonl"

    if turns is None:
        # ── 单轮 ──────────────────────────────────────
        cmd = _build_cmd(binary, model, tools)
        proc = _popen(cmd, cwd, env)
        out = _communicate(proc, prompt, timeout, "")
        canonical_trace.write_text(out, encoding="utf-8")
        return Result(
            result_text=_result_text(out),
            trace_path=str(canonical_trace),
            exit_code=proc.returncode,
        )

    else:
        # ── 多轮（--resume）──────────────────────────
        session_id, texts, exit_code = None, [], 0
        last_out = ""
        for i, turn in enumerate(turns):
            cmd = _build_cmd(binary, model, tools)
            if session_id:
                cmd += ["--resume", session_id]
            proc = _popen(cmd, cwd, env)
            out = _communicate(proc, turn, timeout, f" on turn {i + 1}")
            # 每轮写 caseN.trace.jsonl
            turn_trace = log_dir / f"case{i}.trace.jsonl"
            turn_trace.write_text(out, encoding="utf-8")
            last_out = out
            sid, text = _parse_session(out)
            session_id = session_id or sid
            if i == 0 and len(turns) > 1 and session_id is None:
                raise RuntimeError(
                    "trigger.run: turn 1 produced no session_id; cannot --resume")
            texts.append(text)
            if proc.returncode != 0:
                exit_code = proc.returncode
                break

        # 所有轮 stream-json 事件拼接 → canonical case.trace.jsonl
        # 注：result_text 取各轮文本拼接（含末轮 result）；trace 包含全轮事件，
        # 这样 load_trace/skills_loaded/tools_used 能看到任意一轮的 skill/tool 事件。
        all_turns_content = "".join(
            (log_dir / f"case{i}.trace.jsonl").read_text(encoding="utf-8")
            for i in range(len(texts))
        )
        canonical_trace.write_text(all_turns_content, encoding="utf-8")
        return Result(
            result_text="\n".join(texts),
            trace_path=str(canonical_trace),
            exit_code=exit_code,
        )
