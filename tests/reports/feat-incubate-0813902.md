# Contract Sweep Report

- branch: `feat/incubate` @ `0813902`
- date: 2026-06-10 14:53
- jobs: 1 / total: 379s
- **PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| incubate:incubate#incubate-e2e | PASS | — | 1 | 294s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

I've verified the source file (`工作记录/2026/06/09/task-id-采集探索.md`) exists and the gather faithfully extracted from it. The orphan link `[[机器人工程闭环——采训测推全链路综述]]` genuinely exists in the source and genuinely has no page — findings correctly flags it as a gap, not a fabrication.

```json
{"items": [{"q": "PASS 但产物病态?", "answer": "no", "evidence": "唯一 PASS case incubate-e2e 的 spec.md/findings.md/golden-order.md 均为实质内容:spec 有定位/核心矛盾/3条设计主张/缺口/propose 主张并标注〔资料〕〔想法〕来源;findings 逐条带来源+可信度;golden-order 原文保留两条人类输入。非空话、非复读 prompt、非占位符。"}, {"q": "SKIP 集中某 plugin 形成空洞?", "answer": "no", "evidence": "报告 SKIP 0,Skipped 段为 none,无可集中之处(整个 sweep 仅 1 个 case)。"}, {"q": "FAIL 归因与 detail 不自洽?", "answer": "no", "evidence": "FAIL 0,表格无 FAIL 行,detail 列为空,不存在归因与 detail 冲突的情形。"}, {"q": "NEEDS-REVIEW judge 幻觉?", "answer": "no", "evidence": "NEEDS-REVIEW 0,Overall Verdict 标注 (not run),无 judge 裁决。唯一被标为『不存在』的页 [[机器人工程闭环——采训测推全链路综述]] 经核实确实在源文件 task-id-采集探索.md:16 被引用且 kb 中无对应页,findings 将其正确标为孤儿链接缺口——是准确标注而非幻觉。"}, {"q": "整体可信?", "answer": "yes", "evidence": "断言链(file_exists + verify.sh 的已毕业/golden-order/findings/spec 非空检查)与产物实际内容一致;所有引用(源工作记录、毕业状态、人类想法)均可在 kb 中追溯落地。唯一保留:本次仅 1 个 case、Overall Verdict 未运行,覆盖面极小,可信但样本单薄。"}], "summary": "单 case sweep,PASS 名副其实——产物实质、引用可追溯、唯一『缺失页』是被正确标注的真实缺口而非幻觉;结果可信,但覆盖面仅 1 例、终判未跑,信心受限于样本规模。"}
```
