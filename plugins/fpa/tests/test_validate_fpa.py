#!/usr/bin/env python3
"""validate_fpa.py 新骨架回归测试（自含，无 pytest 依赖）。
运行: python3 plugins/fpa/tests/test_validate_fpa.py  → 全过 exit 0，任一失败 exit 1。
VALID_DOC 保留模块级，可被外部 import 复用，不触发测试执行。
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATE = os.path.join(HERE, "..", "skills", "fpa", "scripts", "validate_fpa.py")

VALID_DOC = """\
---
创建日期: 2026-06-08 00:00
类型: 分析
决策类型: Type 1
---

# First Principles Analysis: 测试主题

## 0. 目标与需求
- 测试目标

## 1. Deconstruct｜拆解：现状 vs 需求
### 对齐映射
| # | 子需求 | 现状由什么满足 | gap |
|---|---|---|---|
| N1 | a | b | c |

## 2. Challenge｜约束三分类
| Claim | Type | Evidence | Challenge |
|---|---|---|---|
| x | Hard constraint | e | q |

## 3. Reconstruct｜重建：最简方案
- 方案

## 4. Validate｜验证与对抗裁决
### 对抗验证裁决
| Target | Verdict | Evidence | 处理 |
|---|---|---|---|
| reconstruction | upheld | ev | 维持 |

## Key Insight
一句话洞察

# References
- 出处
"""


def main():
    def run(path):
        return subprocess.run([sys.executable, VALIDATE, path],
                              capture_output=True, text=True).returncode

    def run_stdin(*args, content):
        return subprocess.run([sys.executable, VALIDATE, *args], input=content,
                              capture_output=True, text=True).returncode

    def write(d, name, content):
        p = os.path.join(d, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    failures = []

    def check(name, cond):
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            failures.append(name)

    with tempfile.TemporaryDirectory() as d:
        check("valid new-skeleton doc PASS", run(write(d, "FPA-valid.md", VALID_DOC)) == 0)
        check("valid MCP stdin doc PASS",
              run_stdin("--stdin-fpa", "FPA-valid.md", content=VALID_DOC) == 0)
        check("invalid MCP stdin doc FAIL",
              run_stdin("--stdin-fpa", "FPA-invalid.md", content="invalid") == 1)
        check("valid MCP stdin suite PASS",
              run_stdin(
                  "--stdin-suite", "valid",
                  content=json.dumps({
                      "fpa": VALID_DOC,
                      "verdicts": {"verdicts": [{"target": "reconstruction", "verdict": "upheld"}]},
                  }),
              ) == 0)

        check("missing 目标与需求 FAIL",
              run(write(d, "FPA-no-goal.md",
                        VALID_DOC.replace("## 0. 目标与需求\n- 测试目标\n\n", ""))) == 1)

        check("bad constraint Type FAIL",
              run(write(d, "FPA-bad-type.md",
                        VALID_DOC.replace("Hard constraint", "随便写的"))) == 1)

        check("missing 对齐映射表 FAIL",
              run(write(d, "FPA-no-align.md",
                        VALID_DOC.replace(
                            "### 对齐映射\n| # | 子需求 | 现状由什么满足 | gap |\n"
                            "|---|---|---|---|\n| N1 | a | b | c |\n", "无表\n"))) == 1)

        check("missing Validate 裁决表 FAIL",
              run(write(d, "FPA-no-verdict.md",
                        VALID_DOC.replace(
                            "### 对抗验证裁决\n| Target | Verdict | Evidence | 处理 |\n"
                            "|---|---|---|---|\n| reconstruction | upheld | ev | 维持 |\n",
                            "无表\n"))) == 1)

        write(d, "FPA-suite.md", VALID_DOC)
        write(d, "adversarial-verdicts.json",
              json.dumps({"verdicts": [{"target": "reconstruction", "verdict": "upheld"}]}))
        check("valid suite RUN-REPORT PASS",
              run(write(d, "RUN-REPORT-suite.md",
                        "# FPA Run Report — suite\n\n裁决计数 upheld 1\n")) == 0)

        write(d, "FPA-suite2.md",
              VALID_DOC.replace("| reconstruction | upheld | ev | 维持 |\n",
                                "| reconstruction | upheld | ev | 维持 |\n"
                                "| assumption-1 | revised | ev | 改 |\n"))
        check("suite verdict-count < Validate 表行数 FAIL",
              run(write(d, "RUN-REPORT-suite2.md",
                        "# FPA Run Report — suite2\n\n裁决计数\n")) == 1)

        # 修复 2：补 3 个最易误删路径的 FAIL 用例
        check("missing H1 FAIL",
              run(write(d, "FPA-no-h1.md",
                        VALID_DOC.replace("# First Principles Analysis: 测试主题\n\n", ""))) == 1)

        check("missing References FAIL",
              run(write(d, "FPA-no-ref.md",
                        VALID_DOC.replace("# References\n- 出处\n", ""))) == 1)

        check("Key Insight empty FAIL",
              run(write(d, "FPA-empty-ki.md",
                        VALID_DOC.replace("## Key Insight\n一句话洞察\n", "## Key Insight\n\n"))) == 1)

    print(f"\n{'ALL PASS' if not failures else 'FAILURES: ' + ', '.join(failures)}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
