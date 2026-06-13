#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""katana contract regression runner. 用法见 tests/run-contracts.sh --help"""
import argparse, json, os, shutil, socket, subprocess, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness.schema import discover_contracts
from harness.case import run_case
from harness.scheduler import schedule
from harness.report import render_report
from harness.judge import run_case_verdict, run_overall_backstop

CCS_HOST, CCS_PORT = "127.0.0.1", 15721


def ccs_online() -> bool:
    try:
        with socket.create_connection((CCS_HOST, CCS_PORT), timeout=2):
            return True
    except OSError:
        return False


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def touched_plugins(repo: Path) -> set:
    base = git(repo, "merge-base", "HEAD", "origin/main") or "HEAD~1"
    diff = git(repo, "diff", "--name-only", base, "HEAD")
    return {p.split("/")[1] for p in diff.splitlines() if p.startswith("plugins/")}


def sweep_setup(repo: Path, tmp: Path, plugins: set, claude_bin: str) -> Path:
    """golden snapshot + 本地 marketplace 安装分支代码 + 冒烟。
    每次 sweep 的 CLAUDE_CONFIG_DIR 都是 fresh mktemp 副本，marketplace add 不存在跨 sweep 幂等性问题。
    """
    golden = tmp / "golden"
    for fx in sorted((repo / "tests/fixtures").iterdir()):
        if fx.is_dir():
            shutil.copytree(fx, golden / fx.name)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(golden / "claude-config")}

    def cc(*args):
        r = subprocess.run([claude_bin, *args], env=env, capture_output=True,
                           text=True, timeout=300)
        if r.returncode != 0:
            sys.exit(f"ABORT sweep-setup: claude {' '.join(args)} failed:\n{r.stdout}{r.stderr}")

    cc("plugin", "marketplace", "add", str(repo))
    for p in sorted(plugins):
        cc("plugin", "install", f"{p}@katana")
    return golden


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--touched", action="store_true")
    ap.add_argument("--case", help="按 skill 或 case_id 过滤，如 wiki:query 或 query-hot")
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
    base_env = {}
    if not args.no_ccs_check:
        if not ccs_online():
            sys.exit("ABORT: ccs (127.0.0.1:15721) offline — 绝不 fallback 直连")
        base_env["ANTHROPIC_BASE_URL"] = f"http://{CCS_HOST}:{CCS_PORT}"
        # claude CLI requires ANTHROPIC_API_KEY to use API-key mode (not OAuth).
        # ccs does not validate incoming tokens; any non-empty string works.
        # Caller may override via ANTHROPIC_AUTH_TOKEN env var.
        base_env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_AUTH_TOKEN", "ccs-local")

    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="katana-contracts."))
    plugins = {c.path.parts[-4] for c in contracts}
    golden = sweep_setup(repo, tmp, plugins, claude_bin)

    def make_job(c):
        def job():
            r = run_case(c, golden, tmp / "cases", claude_bin=claude_bin,
                         base_env=base_env)
            # 契约断言通过后才跑 case verdict（assert-down：verdict 不替代 assert）
            if r.status == "PASS" and c.verdict and not args.skip_judge:
                rubric_key = c.verdict.get("rubric")
                if not rubric_key:
                    r.status = "NEEDS-REVIEW"
                    r.verdict_result = {"error": "verdict.rubric missing in contract", "items": []}
                    return r
                rubric = repo / "tests/judge" / rubric_key
                # 使用 r.case_dir 确保指向实际 PASS 的 attempt 目录（含重试场景）
                case_root = Path(r.case_dir)
                inputs = [Path(str(i).replace("{cwd}", str(case_root / c.cwd))
                                   .replace("{case_log}", str(case_root / "case.log")))
                          for i in c.verdict.get("inputs", [])]
                status, vr = run_case_verdict(rubric=rubric, inputs=inputs,
                                              model=c.model, work_dir=tmp,
                                              claude_bin=claude_bin, base_env=base_env)
                if status != "PASS":
                    r.status, r.verdict_result = "NEEDS-REVIEW", vr
            return r
        job.requires = c.requires
        return job

    results = schedule([make_job(c) for c in contracts], jobs_n=args.jobs,
                       requires_of=lambda j: j.requires)
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    sha = git(repo, "rev-parse", "--short", "HEAD")
    overall = ""
    if not args.skip_judge:
        index = "\n".join(str(p) for p in sorted((tmp / "cases").rglob("*"))[:400])
        overall = run_overall_backstop(
            rubric=repo / "tests/judge/overall-rubric.md",
            report_md=render_report(results, branch=branch, sha=sha,
                                    jobs=args.jobs, total_s=time.monotonic() - t0),
            artifact_index=index, model=os.environ.get("KATANA_CONTRACT_MODEL") or "lingzhi/claude-opus-4-8",
            work_dir=tmp, claude_bin=claude_bin, base_env=base_env)

    md = render_report(results, branch=branch, sha=sha, jobs=args.jobs,
                       total_s=time.monotonic() - t0, overall_verdict=overall)
    out = repo / "tests/reports" / f"{branch.replace('/', '-')}-{sha}.md"
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
