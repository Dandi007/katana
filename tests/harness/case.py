"""单 case 生命周期：requires → 快照 → claude -p → asserts → 重试一次 → 结果。"""
from dataclasses import dataclass, field
from pathlib import Path
import os, shutil, subprocess, time

from .schema import Contract
from .asserts import Ctx, run_asserts
from .claude_cli import run_claude, ClaudeTimeout

@dataclass
class CaseResult:
    case_id: str
    skill: str
    status: str                  # PASS / FAIL / SKIP / NEEDS-REVIEW
    attempts: int = 0
    duration_s: float = 0.0
    model: str = ""
    attribution: str = ""        # env / prompt / model / unknown（FAIL 时必填）
    detail: str = ""
    kept_dir: str = ""
    assert_results: list = field(default_factory=list)
    verdict_result: dict | None = None

def check_requires(requires: list) -> str | None:
    """全部满足返回 None，否则返回第一条不满足原因。"""
    for req in requires:
        kind, _, val = req.partition(":")
        if kind == "exclusive":
            continue
        if kind == "env" and not os.environ.get(val):
            return f"env {val} unset"
        if kind == "dir":
            p = Path(os.path.expandvars(os.path.expanduser(val)))
            if not p.is_dir():
                return f"dir missing: {p}"
        if kind == "cmd" and shutil.which(val) is None:
            return f"cmd missing: {val}"
        if kind == "proc-free":
            if subprocess.run(["pgrep", "-f", val], capture_output=True).returncode == 0:
                return f"process busy: {val}"
    return None

def _snapshot(golden: Path, dest: Path):
    """APFS clonefile 秒级复制；失败回退普通 cp。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cp", "-c", "-R", str(golden), str(dest)],
                       capture_output=True)
    if r.returncode != 0:
        shutil.copytree(golden, dest)

def _attempt(contract: Contract, golden: Path, case_dir: Path,
             claude_bin: str | None, base_env: dict):
    _snapshot(golden, case_dir)
    kb_cwd = case_dir / contract.cwd
    log = case_dir / "case.log"
    env = {**base_env, "CLAUDE_CONFIG_DIR": str(case_dir / "claude-config")}
    res = run_claude(prompt=contract.prompt, cwd=kb_cwd, log_path=log,
                     model=contract.model, permission_mode=contract.permission_mode,
                     allowed_tools=contract.allowed_tools, timeout=contract.timeout,
                     env=env, claude_bin=claude_bin)
    ctx = Ctx(cwd=kb_cwd, stdout=res.stdout, case_log=log,
              contract_dir=contract.path.parent)
    return run_asserts(contract.asserts, ctx), ctx

def run_case(contract: Contract, golden: Path, work_root: Path,
             claude_bin: str | None = None, base_env: dict | None = None) -> CaseResult:
    base_env = base_env or {}
    t0 = time.monotonic()
    reason = check_requires(contract.requires)
    if reason:
        return CaseResult(contract.case_id, contract.skill, "SKIP", detail=reason)
    for attempt in (1, 2):
        case_dir = work_root / (contract.case_id if attempt == 1
                                else f"{contract.case_id}-retry")
        try:
            results, ctx = _attempt(contract, golden, case_dir, claude_bin, base_env)
        except ClaudeTimeout as e:
            if attempt == 2:
                return CaseResult(contract.case_id, contract.skill, "FAIL",
                                  attempts=attempt, attribution="env",
                                  detail=str(e), kept_dir=str(case_dir),
                                  duration_s=time.monotonic() - t0,
                                  model=contract.model)
            continue
        failed = [r for r in results if not r.ok]
        if not failed:
            return CaseResult(contract.case_id, contract.skill, "PASS",
                              attempts=attempt, model=contract.model,
                              duration_s=time.monotonic() - t0,
                              assert_results=[vars(r) for r in results])
        if attempt == 2:
            return CaseResult(contract.case_id, contract.skill, "FAIL",
                              attempts=attempt, attribution="unknown",
                              detail="; ".join(f"{r.type}: {r.detail}" for r in failed),
                              kept_dir=str(case_dir), model=contract.model,
                              duration_s=time.monotonic() - t0,
                              assert_results=[vars(r) for r in results])
    raise AssertionError("unreachable")
