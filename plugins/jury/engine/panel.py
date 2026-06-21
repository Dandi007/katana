#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.10"
# ///
"""jury fanout 引擎：同一 prompt 模板并行打 N 个模型，保留分歧 + 投票。
每模型 = 一次 claude -p，只有 env(由 setter 决定) 与 model 串不同。路由 SSoT
复用 agent-shell 的 set_claude_* 族，引擎不重建路由表。"""
import argparse, concurrent.futures, json, os, re, shlex, subprocess, sys
from pathlib import Path

DEFAULT_PROFILE = os.environ.get("JURY_PROFILE") or os.path.expanduser("~/.config/agent-shell/profile.zsh")
DEFAULT_ROSTER = [
    {"name": "opus", "setter": "set_claude_native_opus", "model": "opus"},
    {"name": "gpt", "setter": "set_claude_ccswitch_gpt", "model": ""},
    {"name": "deepseek", "setter": "set_claude_ccswitch_ds", "model": ""},
    {"name": "qwen", "setter": "set_claude_ccswitch_qwen", "model": ""},
]


def _parse_vote(stream_stdout: str):
    """从 stream-json 末条 result 事件取 result 文本，再抽 fenced json 投票。
    解析不出返回 (None, 原始结果文本)。坏行跳过（韧性）。"""
    result_text = ""
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
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", result_text, re.DOTALL)
    for raw in reversed(blocks):
        try:
            parsed = json.loads(raw)
            if "items" in parsed:
                return parsed, result_text
        except json.JSONDecodeError:
            continue
    return None, result_text


def run_model(member: dict, prompt: str, out_dir: Path, timeout: int,
              profile: str = DEFAULT_PROFILE, target_dir: str = None) -> dict:
    """Run one panel member via claude -p.

    setter must be a shell-safe identifier matching ^set_claude[a-z0-9_]*$;
    any other value raises ValueError to prevent shell injection.

    target_dir: 若给定，子进程在该目录下执行（cwd=target_dir），评审员可 Read 相对路径文件。
    守 G9：评审员只读，allowedTools 仅含 Read/Grep/Glob，不含 Write/Edit/Bash。
    """
    name, setter = member["name"], member["setter"]
    if not re.fullmatch(r"set_claude[a-z0-9_]*", setter):
        raise ValueError(f"unsafe setter: {setter!r}")
    claude_bin = shlex.quote(os.environ.get("JURY_CLAUDE_BIN", "claude"))
    trace = Path(out_dir) / f"{name}.trace.jsonl"
    model_arg = f'--model {shlex.quote(member["model"])}' if member.get("model") else ""
    # 先 source+setter，把 setter 实际产生的 env dump 到 stderr 的 marker 行，
    # 再 exec claude。base_url_used/model_string 读 setter 真实结果，不靠猜。
    # allowedTools 只读三件套（Read/Grep/Glob），守 G9 评审员不可写。
    inner = (
        f'source "{profile}"; {setter} >/dev/null 2>&1; '
        f'echo "__JURY_ENV__ base=${{ANTHROPIC_BASE_URL:-}} model=${{ANTHROPIC_MODEL:-}}" >&2; '
        f'exec {claude_bin} -p {model_arg} --output-format stream-json --verbose --allowedTools "Read,Grep,Glob"'
    )
    # target_dir 给定时，让子进程 cwd=target_dir，评审员可用相对路径 Read 文件。
    subprocess_cwd = target_dir if target_dir else None
    proc = subprocess.run(["zsh", "-c", inner], input=prompt, text=True,
                          capture_output=True, timeout=timeout, cwd=subprocess_cwd)
    trace.write_text(proc.stdout, encoding="utf-8")
    base_url, model_string = "", ""
    mm = re.search(r"__JURY_ENV__ base=(\S*) model=(\S*)", proc.stderr)
    if mm:
        base_url, model_string = mm.group(1), mm.group(2)
    vote, prose = _parse_vote(proc.stdout)
    return {"name": name, "setter": setter, "base_url_used": base_url,
            "model_string": model_string, "exit": proc.returncode,
            "trace_path": str(trace), "vote": vote, "prose": prose}


def _tally(members: list) -> dict:
    """逐 rubric 项多数决；记录分歧。"""
    items = {}
    for r in members:
        if not r.get("vote"):
            continue
        for it in r["vote"].get("items", []):
            q = it.get("q")
            items.setdefault(q, {"q": q, "votes": {}})
            items[q]["votes"][r["name"]] = it.get("answer")
    out = []
    dissent = []
    for q, rec in sorted(items.items()):
        ans = list(rec["votes"].values())
        yes = ans.count("yes")
        rec["majority"] = "yes" if yes * 2 > len(ans) else "no"
        if len(set(ans)) > 1:
            dissent.append(q)
        out.append(rec)
    return {"items": out, "dissent": dissent}


def fanout(prompt: str, out_dir: Path, roster: list, timeout: int,
           profile: str = DEFAULT_PROFILE, target_dir: str = None,
           spec: str = None) -> dict:
    """并行派发所有 roster 成员评审同一 prompt。

    target_dir: 透传给 run_model，评审员子进程在该目录执行（可 Read 相对路径）。
    spec: 若给定，在 prompt 前置 spec 块（评审目标），帮助评审员对照验收标准。
    """
    # spec 非空时，在 prompt 前追加评审目标头，让每个评审员都能看到验收 spec。
    effective_prompt = prompt
    if spec:
        effective_prompt = "## 评审目标（spec）\n" + spec + "\n\n---\n\n" + prompt
    out_dir = Path(out_dir)
    members = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(roster)) as ex:
        futs = {ex.submit(run_model, m, effective_prompt, out_dir, timeout, profile,
                          target_dir): m
                for m in roster}
        for fut, m in futs.items():
            try:
                members.append(fut.result())
            except Exception as e:
                members.append({"name": m["name"], "setter": m["setter"],
                                "base_url_used": "", "model_string": "",
                                "exit": 1, "trace_path": "",
                                "vote": None, "prose": f"FAILED: {e}"})
    ran = [r for r in members if r["exit"] == 0 and r["vote"] is not None]
    quorum = "full" if len(ran) == len(roster) else "partial"
    tally = _tally(members)
    verdict = {**tally, "quorum": quorum,
               "ran": [r["name"] for r in ran]}
    (out_dir / "panel-meta.json").write_text(
        json.dumps([{**{k: r[k] for k in ("name", "setter", "base_url_used",
                     "model_string", "exit", "trace_path")},
                     "vote_parsed": r.get("vote") is not None} for r in members],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "jury-verdict.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    report = ["# jury 多模型评审（保留分歧，未合并）\n"]
    for r in members:
        report.append(f"\n## {r['name']} (`{r['setter']}`, exit={r['exit']})\n")
        report.append(r.get("prose") or "_(无输出)_")
    (out_dir / "jury-report.md").write_text("\n".join(report), encoding="utf-8")
    return {"members": members, "quorum": quorum, "ran": [r["name"] for r in ran], **tally}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--roster")
    ap.add_argument("--profile", default=DEFAULT_PROFILE)
    # 0.2 新增：评审员 cwd + 可选 spec 前置
    ap.add_argument("--target-dir", default=None,
                    help="评审员子进程工作目录（cwd），模型可 Read 相对路径文件")
    ap.add_argument("--spec-file", default=None,
                    help="spec 文件路径，内容将前置到 prompt 作为评审目标")
    a = ap.parse_args()
    roster = json.loads(Path(a.roster).read_text()) if a.roster else DEFAULT_ROSTER
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    spec = Path(a.spec_file).read_text(encoding="utf-8") if a.spec_file else None
    s = fanout(prompt, out, roster, a.timeout, a.profile,
               target_dir=a.target_dir, spec=spec)
    print(json.dumps({"quorum": s["quorum"], "dissent": s["dissent"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
