#!/usr/bin/env python3
"""FPA 文档机械验收器。

两种模式：
  CLI:  python3 validate_fpa.py <FPA-*.md | RUN-REPORT-*.md>   # 失败 exit 1
  Hook: python3 validate_fpa.py --hook              # stdin 读 PostToolUse JSON，
                                                    # 文件名匹配才校验，失败 exit 2

按文件名分两档：
  FPA-<slug>.md        → 结构校验（frontmatter、六 section、表格不变量）
  RUN-REPORT-<slug>.md → 三件套 suite 校验：同级 FPA-<slug>.md 结构通过 +
                         adversarial-verdicts.json 存在且 verdict 条数 ≥
                         FPA 文档 Adversarial Review 表行数。
                         Run report 是流程最后一个产物，挂在它上面校验
                         「过程产物链完整」不会误伤中间状态。

只验机械不变量（结构完整性），不验语义质量——语义由 fpa skill 的
adversarial verify（Workflow skeptics）负责。
"""
from __future__ import annotations
import json
import os
import re
import sys

REQUIRED_FRONTMATTER_KEYS = ["创建日期", "类型", "决策类型"]
REQUIRED_SECTIONS = [
    "Deconstruction",
    "Constraint Classification",
    "Reconstruction",
    "Adversarial Review",
    "Validation / Measurement Plan",
    "Key Insight",
]
TYPE_PATTERN = re.compile(r"hard\s+constraint|soft\s+constraint|assumption", re.IGNORECASE)
FPA_NAME = re.compile(r"^FPA-(.+)\.md$")
RUN_REPORT_NAME = re.compile(r"^RUN-REPORT-(.+)\.md$")


def table_data_rows(section_text: str) -> list[str]:
    """返回 section 内 markdown 表格的数据行（去掉表头与分隔行）。"""
    rows = [l for l in section_text.splitlines() if l.strip().startswith("|")]
    return [r for r in rows[2:] if r.replace("|", "").replace("-", "").replace(":", "").strip()]


def parse_sections(text: str) -> dict[str, str]:
    """按 `## ` 标题切分正文，返回 {section 名: 内容}。"""
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^## +(.+?) *$", text, re.MULTILINE))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1)] = text[m.end():end]
    return sections


def validate(path: str) -> list[str]:
    issues: list[str] = []
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        return [f"无法读取文件: {e}"]

    # frontmatter
    fm = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not fm:
        issues.append("缺少 YAML frontmatter")
    else:
        for key in REQUIRED_FRONTMATTER_KEYS:
            if not re.search(rf"^{key}\s*:\s*\S", fm.group(1), re.MULTILINE):
                issues.append(f"frontmatter 缺少非空字段: {key}")

    # H1
    if not re.search(r"^# First Principles Analysis:\s*\S", text, re.MULTILINE):
        issues.append("缺少 H1: `# First Principles Analysis: <主题>`")

    # sections
    sections = parse_sections(text)
    for name in REQUIRED_SECTIONS:
        if name not in sections:
            issues.append(f"缺少 section: `## {name}`")

    # deconstruction 需求拆解表
    if "Deconstruction" in sections and not table_data_rows(sections["Deconstruction"]):
        issues.append("Deconstruction 缺少需求拆解表（子需求 → 现有方案中由什么满足）")

    # constraint table
    cc = sections.get("Constraint Classification", "")
    if "Constraint Classification" in sections:
        rows = table_data_rows(cc)
        if not rows:
            issues.append("Constraint Classification 表没有数据行")
        else:
            for r in rows:
                cols = [c.strip() for c in r.strip().strip("|").split("|")]
                if len(cols) < 4:
                    issues.append(f"约束表行少于 4 列: {r.strip()[:60]}")
                elif not TYPE_PATTERN.search(cols[1]):
                    issues.append(f"约束表 Type 列不含 hard/soft/assumption: {cols[1][:40]}")
                elif not cols[2]:
                    issues.append(f"约束表 Evidence 列为空: {cols[0][:40]}")

    # adversarial review
    if "Adversarial Review" in sections:
        if not table_data_rows(sections["Adversarial Review"]):
            issues.append("Adversarial Review 表没有数据行（至少须有 reconstruction 一行）")

    # key insight
    if "Key Insight" in sections and not sections["Key Insight"].strip():
        issues.append("Key Insight 为空")

    # references（仓库硬约束：事实性内容文末保留 References）
    ref = re.search(r"^# References\s*$(.*)\Z", text, re.MULTILINE | re.DOTALL)
    if not ref:
        issues.append("缺少文末 `# References`")
    elif not re.search(r"^- \S", ref.group(1), re.MULTILINE):
        issues.append("References 没有条目（至少一条 `- <出处>`）")

    return issues


def validate_suite(report_path: str) -> list[str]:
    """RUN-REPORT-<slug>.md 落盘时校验三件套完整性（过程产物链）。"""
    issues: list[str] = []
    slug = RUN_REPORT_NAME.match(os.path.basename(report_path)).group(1)
    folder = os.path.dirname(os.path.abspath(report_path))

    ar_rows: int | None = None
    fpa_path = os.path.join(folder, f"FPA-{slug}.md")
    if not os.path.isfile(fpa_path):
        issues.append(f"缺少同级 FPA 文档: FPA-{slug}.md（run report 与 FPA 文档 slug 必须一致）")
    else:
        issues += [f"FPA-{slug}.md: {i}" for i in validate(fpa_path)]
        try:
            ar = parse_sections(open(fpa_path, encoding="utf-8").read()).get("Adversarial Review", "")
            ar_rows = len(table_data_rows(ar))
        except OSError:
            pass

    verdicts_path = os.path.join(folder, "adversarial-verdicts.json")
    if not os.path.isfile(verdicts_path):
        issues.append("缺少同级 adversarial-verdicts.json（Phase 2 verdict 原文，正文表只是摘要）")
    else:
        try:
            data = json.load(open(verdicts_path, encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            data = None
            issues.append(f"adversarial-verdicts.json 不是合法 JSON: {e}")
        if data is not None:
            verdicts = data.get("verdicts") if isinstance(data, dict) else data
            if not isinstance(verdicts, list) or not verdicts:
                issues.append("adversarial-verdicts.json 缺少非空 verdicts 数组")
            elif ar_rows is not None and len(verdicts) < ar_rows:
                issues.append(
                    f"verdict 原文条数({len(verdicts)}) < FPA 文档 Adversarial Review 表行数({ar_rows})"
                    "——正文裁决多于原始记录，疑似编造")

    return issues


def dispatch(path: str) -> list[str] | None:
    """按文件名选校验档；不匹配返回 None。"""
    name = os.path.basename(path)
    if FPA_NAME.match(name):
        return validate(path)
    if RUN_REPORT_NAME.match(name):
        return validate_suite(path)
    return None


def main() -> None:
    if "--hook" in sys.argv:
        try:
            payload = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            sys.exit(0)  # 非预期输入不阻塞
        path = (payload.get("tool_input") or {}).get("file_path", "")
        issues = dispatch(path) if path else None
        if issues is None:
            sys.exit(0)
        if issues:
            print("FPA 机械验收失败，缺失项：\n- " + "\n- ".join(issues), file=sys.stderr)
            sys.exit(2)
        sys.exit(0)

    if len(sys.argv) < 2:
        print("usage: validate_fpa.py <FPA-*.md | RUN-REPORT-*.md> | --hook", file=sys.stderr)
        sys.exit(1)
    issues = dispatch(sys.argv[1])
    if issues is None:
        print(f"FAIL: 文件名不匹配 FPA-*.md / RUN-REPORT-*.md: {sys.argv[1]}")
        sys.exit(1)
    if issues:
        print("FAIL:\n- " + "\n- ".join(issues))
        sys.exit(1)
    print("PASS: FPA 机械验收通过")


if __name__ == "__main__":
    main()
