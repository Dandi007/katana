#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""katana contract regression runner v2。六步编排：隔离→快照→触发→delta→三轴→verdict。"""
import argparse, os, shutil, subprocess, sys, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness.schema import discover_contracts, Contract
from harness.isolate import case_clone, case_env, build_base_env, golden_setup
from harness.snapshot import snapshot, delta
from harness.trigger import run as trigger_run, ClaudeTimeout
from harness.expect_process import check_process
from harness.expect_fs import check_fs
from harness.judge import get_judge
from harness.scheduler import schedule
from harness.report import render_report


# ──────────────────────────────────────────────────
# CaseResult（v2 三轴）
# ──────────────────────────────────────────────────

@dataclass
class CaseResult:
    case_id: str
    skill: str
    status: str                  # PASS / FAIL / NEEDS-REVIEW / ERROR / SKIP
    attempts: int = 0
    duration_s: float = 0.0
    model: str = ""
    attribution: str = ""        # env / prompt / model / unknown（FAIL 时填）
    detail: str = ""
    kept_dir: str = ""
    case_dir: str = ""           # 实际产物目录（PASS 时为成功 attempt 的目录）
    # 三轴结果详情：{"process": [...], "filesystem": [...], "semantic": {...}}
    axis_detail: dict = field(default_factory=dict)
    # 向后兼容旧 report 字段
    verdict_result: dict | None = None


# ──────────────────────────────────────────────────
# 占位符解析
# ──────────────────────────────────────────────────

def _resolve_verdict_inputs(raw_inputs, case_root, cwd, delta_info=None) -> list:
    """把 semantic.inputs 列表里的占位符替换成真实 Path。

    占位符：
      {case_trace}  → case_root/case.trace.jsonl
      {cwd}         → case_root/<cwd>
      {case_log}    → case_root/case.log
      created       → delta_info["created"] 里所有文件的路径列表
    """
    out = []
    for i in raw_inputs:
        if i == "created":
            # 展开 delta.created 里所有文件；无 delta 信息时跳过（不留字面 "created"）
            if delta_info is not None:
                for rel in sorted(delta_info.get("created", [])):
                    out.append(Path(case_root) / cwd / rel)
            continue
        s = (str(i)
             .replace("{case_trace}", str(Path(case_root) / "case.trace.jsonl"))
             .replace("{cwd}", str(Path(case_root) / cwd))
             .replace("{case_log}", str(Path(case_root) / "case.log")))
        out.append(Path(s))
    return out


# ──────────────────────────────────────────────────
# 六步 run_case
# ──────────────────────────────────────────────────

def run_case(
    contract: Contract,
    golden: Path,
    work_root: Path,
    base_env: dict,
    models: dict,
    claude_bin: str | None = None,
    skip_judge: bool = False,
) -> CaseResult:
    """六步编排：隔离→快照 before→触发→快照 after+delta→三轴→verdict + retry-once（infra flake）。

    硬 FAIL 只来自轴①②（确定性）；轴③语义失败→NEEDS-REVIEW（G1）。
    model 永远显式 --model（G5）。
    skip_judge=True 时显式跳过轴③（--skip-judge / 非 semantic 契约路径）。
    """
    binary = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    t0 = time.monotonic()

    # requires 检查（SKIP）
    skip_reason = _check_requires(contract.requires)
    if skip_reason:
        return CaseResult(contract.case_id, contract.skill, "SKIP",
                          detail=skip_reason, model=contract.model)

    for attempt in (1, 2):
        suffix = "" if attempt == 1 else "-retry"
        case_dir = Path(work_root) / f"{contract.case_id}{suffix}"

        try:
            result = _attempt(contract, golden, case_dir, base_env, models, binary,
                              skip_judge=skip_judge)
        except ClaudeTimeout as e:
            # infra flake：超时 retry-once
            if attempt == 1:
                continue
            return CaseResult(
                contract.case_id, contract.skill, "FAIL",
                attempts=attempt, attribution="env",
                detail=str(e), kept_dir=str(case_dir),
                case_dir=str(case_dir),
                duration_s=time.monotonic() - t0,
                model=contract.model,
            )
        except Exception as e:
            return CaseResult(
                contract.case_id, contract.skill, "ERROR",
                attempts=attempt, attribution="unknown",
                detail=f"unexpected error: {e}",
                kept_dir=str(case_dir), case_dir=str(case_dir),
                duration_s=time.monotonic() - t0,
                model=contract.model,
            )

        status, detail, axis_detail, verdict_result = result

        if status == "PASS":
            return CaseResult(
                contract.case_id, contract.skill, "PASS",
                attempts=attempt, model=contract.model,
                case_dir=str(case_dir),
                duration_s=time.monotonic() - t0,
                axis_detail=axis_detail,
            )
        if status == "NEEDS-REVIEW":
            return CaseResult(
                contract.case_id, contract.skill, "NEEDS-REVIEW",
                attempts=attempt, model=contract.model,
                case_dir=str(case_dir),
                duration_s=time.monotonic() - t0,
                detail=detail, axis_detail=axis_detail,
                verdict_result=verdict_result,
            )
        # status == "FAIL"：轴①②确定性断言失败，立即返回，不重试（retry 只给 infra flake / ClaudeTimeout）
        return CaseResult(
            contract.case_id, contract.skill, "FAIL",
            attempts=attempt, attribution="unknown",
            detail=detail, kept_dir=str(case_dir),
            case_dir=str(case_dir),
            duration_s=time.monotonic() - t0,
            model=contract.model,
            axis_detail=axis_detail,
        )

    raise AssertionError("unreachable")


def _attempt(contract, golden, case_dir, base_env, models, binary, skip_judge: bool = False):
    """单次尝试，返回 (status, detail, axis_detail, verdict_result)。"""
    case_dir = Path(case_dir)

    # 步骤 1：隔离克隆
    case_clone(golden, case_dir)
    cwd = case_dir / contract.fixture
    env = case_env(base_env, case_dir)

    # 步骤 2：before 快照
    before = snapshot(cwd)

    # 步骤 3：触发 claude
    prompt = contract.prompt or None
    turns = contract.turns or None
    if prompt and turns:
        raise ValueError("contract has both prompt and turns")
    if not prompt and not turns:
        raise ValueError("contract has neither prompt nor turns")
    res = trigger_run(
        prompt=prompt,
        turns=turns,
        cwd=cwd,
        log_dir=case_dir,
        model=contract.model,    # 显式传 model（G5）
        tools=contract.tools,
        timeout=contract.timeout,
        env=env,
        claude_bin=binary,
    )

    # 步骤 4：after 快照 + delta
    after = snapshot(cwd)
    d = delta(before, after)

    # 步骤 5：轴① 过程断言（硬）
    proc_results = check_process(contract.process, res.trace_path)
    proc_failed = [r for r in proc_results if not r.ok]

    # 步骤 5：轴② 产物 delta 断言（硬）
    fs_results = check_fs(contract.filesystem, d, cwd, contract.path.parent)
    fs_failed = [r for r in fs_results if not r.ok]

    axis_detail = {
        "process": [{"type": r.type, "ok": r.ok, "detail": r.detail} for r in proc_results],
        "filesystem": [{"type": r.type, "ok": r.ok, "detail": r.detail} for r in fs_results],
        "delta": {k: sorted(v) for k, v in d.items()},
    }

    # 任一硬断言失败 → FAIL（确定性闸门）
    if proc_failed or fs_failed:
        fail_details = (
            [f"process/{r.type}: {r.detail}" for r in proc_failed] +
            [f"fs/{r.type}: {r.detail}" for r in fs_failed]
        )
        return "FAIL", "; ".join(fail_details), axis_detail, None

    # 步骤 5：轴③ 语义 judge（软，仅 PASS 后运行；skip_judge=True 时显式跳过）
    if contract.semantic and not skip_judge and models.get("semantic_judge"):
        judge_name = models.get("semantic_judge", "single")
        judge_setter, judge_model = _judge_role(models)
        rubric_key = contract.semantic.get("rubric", "")
        if not rubric_key:
            axis_detail["semantic"] = {"error": "semantic.rubric missing"}
            return "NEEDS-REVIEW", "semantic.rubric missing", axis_detail, None

        # rubric 路径：相对 contract 所在目录
        rubric = contract.path.parent / rubric_key
        raw_inputs = contract.semantic.get("inputs", [])
        inputs = _resolve_verdict_inputs(raw_inputs, case_dir, contract.fixture, d)

        try:
            judge = get_judge(judge_name)
            status_j, verdict = judge.judge(
                rubric=rubric,
                inputs=inputs,
                model=judge_model,
                work_dir=case_dir,
                env=env,
                claude_bin=binary,
            )
        except NotImplementedError as e:
            axis_detail["semantic"] = {"error": str(e)}
            return "NEEDS-REVIEW", f"judge not implemented: {e}", axis_detail, None
        except Exception as e:
            axis_detail["semantic"] = {"error": str(e)}
            return "NEEDS-REVIEW", f"judge error: {e}", axis_detail, None

        axis_detail["semantic"] = verdict
        if status_j != "PASS":
            return "NEEDS-REVIEW", "semantic judge non-PASS", axis_detail, verdict

    return "PASS", "", axis_detail, None


def _judge_role(models: dict):
    """从 models dict 取 default-judge 的 (setter, model)，无则用默认。"""
    roles = models.get("roles", {})
    role_cfg = roles.get("default-judge", {})
    setter = role_cfg.get("setter", "")
    model = role_cfg.get("model", "lingzhi/claude-opus-4-8")
    return setter, model


def _check_requires(requires: list) -> str | None:
    """全部满足返回 None，否则返回第一条不满足原因。未知 kind 立即 raise（防 typo 假 PASS）。"""
    import shutil as _shutil
    for req in requires:
        kind, _, val = req.partition(":")
        if kind == "exclusive":
            continue  # 由 scheduler 处理
        elif kind == "env":
            if not os.environ.get(val):
                return f"env {val} unset"
        elif kind == "dir":
            p = Path(os.path.expandvars(os.path.expanduser(val)))
            if not p.is_dir():
                return f"dir missing: {p}"
        elif kind == "cmd":
            if _shutil.which(val) is None:
                return f"cmd missing: {val}"
        elif kind == "proc-free":
            import subprocess as _sp
            if _sp.run(["pgrep", "-f", val], capture_output=True).returncode == 0:
                return f"process busy: {val}"
        else:
            raise ValueError(f"unknown requires kind: {kind!r}")
    return None


# ──────────────────────────────────────────────────
# Git helpers
# ──────────────────────────────────────────────────

def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def touched_plugins(repo: Path) -> set:
    base = git(repo, "merge-base", "HEAD", "origin/main") or "HEAD~1"
    diff = git(repo, "diff", "--name-only", base, "HEAD")
    return {p.split("/")[1] for p in diff.splitlines() if p.startswith("plugins/")}


# ──────────────────────────────────────────────────
# CLI main
# ──────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="katana contract runner v2")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--touched", action="store_true")
    ap.add_argument("--case", help="按 skill 或 case_id 过滤")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--skip-judge", action="store_true")
    ap.add_argument("--no-ccs-check", action="store_true",
                    help="单测/fake-claude 用；真实运行禁用")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    contracts = discover_contracts(repo)

    if args.validate_only:
        print(f"{len(contracts)} contracts valid")
        return

    if args.case:
        contracts = [c for c in contracts if args.case in (c.skill, c.case_id)]
    elif args.touched:
        tp = touched_plugins(repo)
        contracts = [c for c in contracts if c.path.parts[-4] in tp]
    elif not args.all:
        sys.exit("choose one of --all / --touched / --case")

    if not contracts:
        if args.case:
            sys.exit(f"ERROR: --case {args.case!r} matched no contracts")
        print("no contracts selected (no touched plugins)")
        return

    claude_bin = os.environ.get("CLAUDE_BIN", "claude")
    base_env = build_base_env(args.no_ccs_check)

    # 加载模型配置（tests/models.yaml）
    try:
        from harness.model import load_models
        models = load_models(repo)
    except Exception:
        models = {"semantic_judge": "single", "roles": {}}

    skip_judge = args.skip_judge  # 显式标志，直接传给 run_case（I2）

    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="katana-contracts."))
    plugins = {c.path.parts[-4] for c in contracts}

    try:
        golden = golden_setup(repo, tmp, plugins, claude_bin)
    except SystemExit:
        raise

    def make_job(c):
        def job():
            return run_case(c, golden, tmp / "cases",
                            base_env=base_env, models=models,
                            claude_bin=claude_bin, skip_judge=skip_judge)
        job.requires = c.requires
        return job

    results = schedule([make_job(c) for c in contracts], jobs_n=args.jobs,
                       requires_of=lambda j: j.requires)

    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    sha = git(repo, "rev-parse", "--short", "HEAD")
    total_s = time.monotonic() - t0

    md = render_report(results, branch=branch, sha=sha,
                       jobs=args.jobs, total_s=total_s)
    out = repo / "tests/reports" / f"{branch.replace('/', '-')}-{sha}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nreport: {out}")

    fails = [r for r in results if r.status == "FAIL"]
    if not fails and not os.environ.get("KEEP_WORK_DIR"):
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"work dir kept: {tmp}")

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
