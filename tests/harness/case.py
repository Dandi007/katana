"""单 case 生命周期：requires → 快照 → claude -p → asserts → 重试一次 → 结果。"""
from dataclasses import dataclass, field
from pathlib import Path
import os, shutil, subprocess, time

from .schema import Contract
from .asserts import Ctx, run_asserts
from .claude_cli import run_claude, run_claude_session, ClaudeTimeout

KNOWN_REQUIRE_KINDS = {"env", "dir", "cmd", "proc-free", "exclusive"}

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
    case_dir: str = ""           # 实际产物目录（PASS 时为成功 attempt 的目录）
    assert_results: list = field(default_factory=list)
    verdict_result: dict | None = None

def check_requires(requires: list) -> str | None:
    """全部满足返回 None，否则返回第一条不满足原因。"""
    for req in requires:
        kind, _, val = req.partition(":")
        if kind not in KNOWN_REQUIRE_KINDS:
            raise ValueError(f"unknown requires kind: {kind!r} in {req!r}")
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
    """APFS clonefile 秒级复制；失败回退普通 cp。dest 残留先清（防嵌套复制）。"""
    if dest.exists():
        shutil.rmtree(dest)
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
    # HOME 隔离：防 ~/.katana 兜底命中真实文件（KATANA_KB_ROOT 已在 build_base_env 置空，
    # 双重保险；隔离 HOME 同时防其他 ~ 展开路径误命中宿主配置）。
    home = case_dir / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {**base_env, "HOME": str(home),
           "CLAUDE_CONFIG_DIR": str(case_dir / "claude-config")}
    if contract.turns:
        res = run_claude_session(
            turns=contract.turns, cwd=kb_cwd, log_path=log,
            model=contract.model, permission_mode=contract.permission_mode,
            allowed_tools=contract.allowed_tools, no_tools=contract.no_tools, timeout=contract.timeout,
            env=env, claude_bin=claude_bin)
    else:
        res = run_claude(
            prompt=contract.prompt, cwd=kb_cwd, log_path=log,
            model=contract.model, permission_mode=contract.permission_mode,
            allowed_tools=contract.allowed_tools, no_tools=contract.no_tools, timeout=contract.timeout,
            env=env, claude_bin=claude_bin)
    ctx = Ctx(cwd=kb_cwd, stdout=res.stdout, case_log=log,
              contract_dir=contract.path.parent,
              trace_path=getattr(res, "trace_path", None))
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
                                  case_dir=str(case_dir),
                                  duration_s=time.monotonic() - t0,
                                  model=contract.model)
            continue
        failed = [r for r in results if not r.ok]
        if not failed:
            return CaseResult(contract.case_id, contract.skill, "PASS",
                              attempts=attempt, model=contract.model,
                              case_dir=str(case_dir),
                              duration_s=time.monotonic() - t0,
                              assert_results=[vars(r) for r in results])
        if attempt == 2:
            return CaseResult(contract.case_id, contract.skill, "FAIL",
                              attempts=attempt, attribution="unknown",
                              detail="; ".join(f"{r.type}: {r.detail}" for r in failed),
                              kept_dir=str(case_dir), case_dir=str(case_dir),
                              model=contract.model,
                              duration_s=time.monotonic() - t0,
                              assert_results=[vars(r) for r in results])
    raise AssertionError("unreachable")
