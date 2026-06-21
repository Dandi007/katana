"""claude -p 调用封装。已知坑只在这里解一次：
- prompt 走 stdin（--add-dir 等变长参数会吞位置参数 prompt）
- stdout(stream-json 事件流) 写 case.trace.jsonl；result 文本写 case.log
- 超时杀整个进程组（start_new_session + killpg）
"""
from dataclasses import dataclass, field
from pathlib import Path
import json, os, signal, subprocess

class ClaudeTimeout(Exception):
    pass

@dataclass
class ClaudeResult:
    exit_code: int
    stdout: str
    trace_path: str = ""


def _result_text(stream_stdout: str) -> str:
    """从 stream-json 输出取末条 type==result 事件的 result 字段。
    坏行/空行跳过（韧性）。找不到 result 事件则原样返回整段输出（向后兼容）。"""
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
    if result_text is None:
        # 无 result 事件（旧格式 / 非 stream-json 输出），原样返回
        return stream_stdout
    return result_text


def run_claude(*, prompt: str, cwd: Path, log_path: Path, model: str,
               permission_mode: str, allowed_tools: list, timeout: int,
               env: dict, claude_bin: str | None = None,
               no_tools: bool = False) -> ClaudeResult:
    cmd = [claude_bin or os.environ.get("CLAUDE_BIN", "claude"), "-p",
           "--model", model, "--permission-mode", permission_mode,
           "--output-format", "stream-json", "--verbose"]
    if no_tools:
        # 描述类契约：allowlist 只放 Skill（加载被测 skill），其余工具全禁 → 防 agentic 长循环
        cmd += ["--tools", "Skill"]
    elif allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    full_env = {**os.environ, **env}
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=full_env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        out, _ = proc.communicate(input=prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait()
        if proc.stdout: proc.stdout.close()
        if proc.stdin: proc.stdin.close()
        raise ClaudeTimeout(f"timeout {timeout}s")
    # 原始 stream-json 事件流 → case.trace.jsonl
    trace_path = Path(log_path).with_name("case.trace.jsonl")
    trace_path.write_text(out, encoding="utf-8")
    # result 文本 → case.log（便于人工阅读和 stdout_grep 断言）
    result_text = _result_text(out)
    Path(log_path).write_text(result_text, encoding="utf-8")
    return ClaudeResult(exit_code=proc.returncode, stdout=result_text,
                        trace_path=str(trace_path))


def _parse_session(stream_stdout: str):
    """从 stream-json 输出取 (session_id, result 文本)。
    取首条含 session_id 的事件作 session_id；末条 result 事件的 result 作文本。
    非 stream-json 格式（兼容旧 --output-format json）时尝试单行 JSON 解析。"""
    session_id = None
    result_text = None
    lines = stream_stdout.splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        # 取首条有 session_id 的事件
        if session_id is None and ev.get("session_id"):
            session_id = ev["session_id"]
        # 取末条 result 事件的 result 文本
        if ev.get("type") == "result":
            result_text = ev.get("result", "") or ""
    if result_text is not None:
        return session_id, result_text
    # 未找到 result 事件：可能是单行 json（旧格式）或纯文本，原样返回
    return session_id, stream_stdout


def run_claude_session(*, turns: list, cwd: Path, log_path: Path, model: str,
                       permission_mode: str, allowed_tools: list, timeout: int,
                       env: dict, claude_bin: str | None = None,
                       no_tools: bool = False) -> ClaudeResult:
    """多轮：同一 session/cwd 顺序续跑，work folder 跨轮累积。
    turn1 取 session_id；turn2..N 带 --resume <session_id>。任一轮非零退出即中止。
    每轮 stream-json 写 caseN.trace.jsonl；末轮 trace 同时写 case.trace.jsonl（供 trace_* 断言）。"""
    binary = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    full_env = {**os.environ, **env}
    session_id, texts, exit_code = None, [], 0
    last_trace_path = ""
    for i, turn in enumerate(turns):
        cmd = [binary, "-p", "--model", model,
               "--permission-mode", permission_mode,
               "--output-format", "stream-json", "--verbose"]
        if session_id:
            cmd += ["--resume", session_id]
        if no_tools:
            # 描述类契约：allowlist 只放 Skill（加载被测 skill），其余工具全禁 → 防 agentic 长循环
            cmd += ["--tools", "Skill"]
        elif allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=full_env,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
        try:
            out, _ = proc.communicate(input=turn, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()
            if proc.stdout: proc.stdout.close()
            if proc.stdin: proc.stdin.close()
            raise ClaudeTimeout(f"timeout {timeout}s on turn {i + 1}")
        # 每轮 trace 写到 caseN.trace.jsonl（N=0起）
        turn_trace = Path(log_path).with_name(f"case{i}.trace.jsonl")
        turn_trace.write_text(out, encoding="utf-8")
        last_trace_path = str(turn_trace)
        sid, text = _parse_session(out)
        session_id = session_id or sid
        if i == 0 and len(turns) > 1 and session_id is None:
            raise RuntimeError(
                "run_claude_session: turn 1 produced no session_id (non-JSON output?); "
                "cannot --resume subsequent turns")
        texts.append(text)
        if proc.returncode != 0:
            exit_code = proc.returncode
            break
    # 末轮 trace 同时写 case.trace.jsonl（trace_* 断言的统一入口）
    canonical_trace = Path(log_path).with_name("case.trace.jsonl")
    if last_trace_path:
        canonical_trace.write_text(
            Path(last_trace_path).read_text(encoding="utf-8"), encoding="utf-8")
    combined = "\n".join(texts)
    Path(log_path).write_text(combined, encoding="utf-8")
    return ClaudeResult(exit_code=exit_code, stdout=combined,
                        trace_path=str(canonical_trace))
