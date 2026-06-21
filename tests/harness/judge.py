"""轴③ 可插拔 judge。

SingleJudge（默认）：rubric+inputs → fenced json → 任何非 yes = NEEDS-REVIEW。
JuryJudge：stub，后续 MR 接入 jury plugin。

用法：
    judge = get_judge("single")
    status, verdict = judge.judge(rubric, inputs, model, work_dir, env, claude_bin)
"""
import json
import re
from pathlib import Path

from . import trigger


# ──────────────────────────────────────────────────
# 公共工具：fenced json 解析
# ──────────────────────────────────────────────────

def parse_verdict_json(text: str) -> dict:
    """从文本中提取 fenced json 格式的 verdict。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        raise ValueError("no fenced json in judge output")
    return json.loads(m.group(1))


# ──────────────────────────────────────────────────
# Judge 协议（duck typing，不强迫继承）
# ──────────────────────────────────────────────────

class SingleJudge:
    """单模型 judge：搬旧 run_case_verdict 逻辑，改走 trigger.run。"""

    def judge(
        self,
        rubric,
        inputs: list,
        model: str,
        work_dir,
        env: dict,
        claude_bin=None,
    ) -> tuple[str, dict]:
        """
        运行 case-level verdict：读 rubric 与产物，返回 (status, verdict_dict)。

        Status:
        - PASS: 所有项都是 yes
        - NEEDS-REVIEW: 任何项是 no 或解析/执行失败

        Verdict dict 包含 items（裁决项）与可选 error（解析失败时）。
        """
        rubric = Path(rubric)
        work_dir = Path(work_dir)
        try:
            prompt = _judge_prompt(rubric, inputs)
            res = trigger.run(
                prompt=prompt,
                cwd=work_dir,
                log_dir=work_dir,
                model=model,
                tools=["Read"],
                timeout=300,
                env=env or {},
                claude_bin=claude_bin,
            )
            verdict = parse_verdict_json(res.result_text)
        except Exception as e:
            return "NEEDS-REVIEW", {"error": f"judge parse/run failed: {e}", "items": []}

        bad = [i for i in verdict.get("items", []) if i.get("answer") != "yes"]
        return ("NEEDS-REVIEW" if bad else "PASS"), verdict


class JuryJudge:
    """Jury 多模型 judge（接缝 stub，后续 MR 接入 jury plugin）。"""

    def judge(self, *args, **kwargs):
        raise NotImplementedError("jury adapter: 后续 MR")


# ──────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────

_REGISTRY: dict[str, object] = {
    "single": SingleJudge(),
    "jury": JuryJudge(),
}


def get_judge(name: str):
    """按名称查 judge 实例。未知名报错。"""
    if name not in _REGISTRY:
        raise KeyError(f"unknown judge: {name!r}. available: {list(_REGISTRY)}")
    return _REGISTRY[name]


# ──────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────

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


# ──────────────────────────────────────────────────
# 向后兼容：保留旧顶层函数（旧测试可继续 import）
# ──────────────────────────────────────────────────

def run_case_verdict(
    *,
    rubric,
    inputs: list,
    model: str,
    work_dir,
    claude_bin=None,
    base_env=None,
) -> tuple[str, dict]:
    """旧顶层函数，委托给 SingleJudge（向后兼容）。"""
    return get_judge("single").judge(
        rubric=rubric,
        inputs=inputs,
        model=model,
        work_dir=work_dir,
        env=base_env or {},
        claude_bin=claude_bin,
    )
