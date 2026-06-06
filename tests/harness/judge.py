"""Prompt 验收两层：case verdict（难契约 skill）+ overall backstop（sweep 级）。
judge 未经 meta-eval 校准：任何 no / 解析失败 → NEEDS-REVIEW，绝不直接 FAIL。"""
import json
import re
from pathlib import Path
from .claude_cli import run_claude


def parse_verdict_json(text: str) -> dict:
    """从文本中提取 fenced json 格式的 verdict。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        raise ValueError("no fenced json in judge output")
    return json.loads(m.group(1))


def _judge_prompt(rubric: Path, inputs: list) -> str:
    """构造 judge prompt：rubric + 输入文件内容。"""
    parts = [
        "你是验收 judge。逐项核对 rubric 并输出 fenced json "
        '{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}。\n',
        "## Rubric\n",
        rubric.read_text(encoding="utf-8"),
    ]
    for p in inputs:
        body = Path(p).read_text(encoding="utf-8")[:20000]
        parts.append(f"\n## Input: {Path(p).name}\n````\n{body}\n````")
    return "\n".join(parts)


def run_case_verdict(
    *,
    rubric: Path,
    inputs: list,
    model: str,
    work_dir: Path,
    claude_bin=None,
    base_env=None,
) -> tuple[str, dict]:
    """
    运行 case-level verdict：读 rubric 与产物，返回 (status, verdict_dict)。

    Status:
    - PASS: 所有项都是 yes
    - NEEDS-REVIEW: 任何项是 no 或解析/执行失败

    Verdict dict 包含 items（裁决项）与可选 error（解析失败时）。
    """
    try:
        res = run_claude(
            prompt=_judge_prompt(Path(rubric), inputs),
            cwd=work_dir,
            log_path=Path(work_dir) / "judge.log",
            model=model,
            permission_mode="default",
            allowed_tools=["Read"],
            timeout=300,
            env=base_env or {},
            claude_bin=claude_bin,
        )
        verdict = parse_verdict_json(res.stdout)
    except Exception as e:
        return "NEEDS-REVIEW", {"error": f"judge parse/run failed: {e}", "items": []}

    bad = [i for i in verdict.get("items", []) if i.get("answer") != "yes"]
    return ("NEEDS-REVIEW" if bad else "PASS"), verdict


def run_overall_backstop(
    *,
    rubric: Path,
    report_md: str,
    artifact_index: str,
    model: str,
    work_dir: Path,
    claude_bin=None,
    base_env=None,
) -> str:
    """
    运行 sweep-level overall backstop：读全量报告与产物索引，返回原始文本响应。

    失败时返回带下划线的错误消息（便于测试与日志混合）。
    """
    prompt = (
        f"{Path(rubric).read_text(encoding='utf-8')}\n\n"
        f"## Sweep Report\n````\n{report_md[:40000]}\n````\n"
        f"## Artifact Index\n````\n{artifact_index[:10000]}\n````"
    )
    try:
        res = run_claude(
            prompt=prompt,
            cwd=work_dir,
            log_path=Path(work_dir) / "overall-judge.log",
            model=model,
            permission_mode="default",
            allowed_tools=["Read"],
            timeout=600,
            env=base_env or {},
            claude_bin=claude_bin,
        )
        return res.stdout.strip()
    except Exception as e:
        return f"_(overall backstop failed: {e})_"
