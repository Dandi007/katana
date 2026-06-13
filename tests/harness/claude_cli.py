"""claude -p 调用封装。已知坑只在这里解一次：
- prompt 走 stdin（--add-dir 等变长参数会吞位置参数 prompt）
- stdout+stderr 合流 tee 到 case.log
- 超时杀整个进程组（start_new_session + killpg）
"""
from dataclasses import dataclass
from pathlib import Path
import json, os, signal, subprocess

class ClaudeTimeout(Exception):
    pass

@dataclass
class ClaudeResult:
    exit_code: int
    stdout: str

def run_claude(*, prompt: str, cwd: Path, log_path: Path, model: str,
               permission_mode: str, allowed_tools: list, timeout: int,
               env: dict, claude_bin: str | None = None,
               no_tools: bool = False) -> ClaudeResult:
    cmd = [claude_bin or os.environ.get("CLAUDE_BIN", "claude"), "-p",
           "--model", model, "--permission-mode", permission_mode]
    if no_tools:
        # 描述类契约：禁文件/执行工具防 agentic 长循环，保留 Skill 工具以加载被测 skill
        cmd += ["--disallowedTools", "Bash,Edit,Write,Read,Grep,Glob"]
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
    Path(log_path).write_text(out, encoding="utf-8")
    return ClaudeResult(exit_code=proc.returncode, stdout=out)


def _parse_session(out: str):
    """从 --output-format json 输出取 (session_id, result 文本)；非 JSON 回退整段。"""
    try:
        obj = json.loads(out)
    except json.JSONDecodeError:
        return None, out
    return obj.get("session_id"), obj.get("result", "")


def run_claude_session(*, turns: list, cwd: Path, log_path: Path, model: str,
                       permission_mode: str, allowed_tools: list, timeout: int,
                       env: dict, claude_bin: str | None = None,
                       no_tools: bool = False) -> ClaudeResult:
    """多轮：同一 session/cwd 顺序续跑，work folder 跨轮累积。
    turn1 取 session_id；turn2..N 带 --resume <session_id>。任一轮非零退出即中止。"""
    binary = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    full_env = {**os.environ, **env}
    session_id, texts, exit_code = None, [], 0
    for i, turn in enumerate(turns):
        cmd = [binary, "-p", "--model", model,
               "--permission-mode", permission_mode, "--output-format", "json"]
        if session_id:
            cmd += ["--resume", session_id]
        if no_tools:
            # 描述类契约：禁文件/执行工具防 agentic 长循环，保留 Skill 工具以加载被测 skill
            cmd += ["--disallowedTools", "Bash,Edit,Write,Read,Grep,Glob"]
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
    combined = "\n".join(texts)
    Path(log_path).write_text(combined, encoding="utf-8")
    return ClaudeResult(exit_code=exit_code, stdout=combined)
