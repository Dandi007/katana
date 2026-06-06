"""claude -p 调用封装。已知坑只在这里解一次：
- prompt 走 stdin（--add-dir 等变长参数会吞位置参数 prompt）
- stdout+stderr 合流 tee 到 case.log
- 超时杀整个进程组（start_new_session + killpg）
"""
from dataclasses import dataclass
from pathlib import Path
import os, signal, subprocess

class ClaudeTimeout(Exception):
    pass

@dataclass
class ClaudeResult:
    exit_code: int
    stdout: str

def run_claude(*, prompt: str, cwd: Path, log_path: Path, model: str,
               permission_mode: str, allowed_tools: list, timeout: int,
               env: dict, claude_bin: str | None = None) -> ClaudeResult:
    cmd = [claude_bin or os.environ.get("CLAUDE_BIN", "claude"), "-p",
           "--model", model, "--permission-mode", permission_mode]
    if allowed_tools:
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
        raise ClaudeTimeout(f"timeout {timeout}s")
    Path(log_path).write_text(out, encoding="utf-8")
    return ClaudeResult(exit_code=proc.returncode, stdout=out)
