# Contract Sweep Report

- branch: `feat/writing-template-layer` @ `1d6e49a`
- date: 2026-06-13 22:34
- jobs: 4 / total: 225s
- **PASS 7 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| writing:bluf#bluf-structure | PASS | — | 1 | 25s | lingzhi/qwen3.7-max |  |
| writing:readability-check#distill-mode | PASS | — | 1 | 34s | lingzhi/qwen3.7-max |  |
| writing:readability-check#evolve-triage | PASS | — | 1 | 28s | lingzhi/claude-opus-4-8 |  |
| writing:readability-check#readability-check-workflow | PASS | — | 1 | 34s | lingzhi/qwen3.7-max |  |
| writing:using-writing#using-writing-injected | PASS | — | 1 | 8s | lingzhi/qwen3.7-max |  |
| writing:write#write-smoke | PASS | — | 1 | 19s | lingzhi/qwen3.7-max |  |
| writing:write#write-template-instantiate | PASS | — | 2 | 79s | lingzhi/qwen3.7-max |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

```json
{"items": [{"q": "有没有断言全绿（PASS）但产物内容明显病态的 case（空话文件、复读 prompt、占位符正文）？", "answer": "yes", "evidence": "write-template-instantiate/case.log 仅 1 行，内容为 '（后台任务已结束，内容已在上面的回答中用到了。有新问题随时问。）'，属于典型占位符/空话文件。虽然存在 write-template-instantiate-retry/case.log（54 行实质内容），但主产物文件病态，表明 artifact 收集或 verdict 判定存在缺陷。其余 6 个 PASS case（bluf-structure 38行、distill-mode、evolve-triage、readability-check-workflow、using-writing-injected、write-smoke）产物内容均健康。"}, {"q": "SKIP 的 case 是否集中到了某个 plugin，形成覆盖空洞？", "answer": "no", "evidence": "报告显示 SKIP 0，无 SKIP case。但需注意本次 sweep 仅覆盖 writing plugin 的 7 个 case，未测试其他 plugin，属于 scope 限制而非 SKIP 集中问题。"}, {"q": "FAIL case 的归因标注与 detail 是否自洽（归因 env 但 detail 像 prompt 缺陷，属于不自洽）？", "answer": "no", "evidence": "报告显示 FAIL 0，无 FAIL case 可供评估归因自洽性。"}, {"q": "NEEDS-REVIEW 的 judge 裁决里有没有引用不存在文件/页面的幻觉迹象？", "answer": "no", "evidence": "报告显示 NEEDS-REVIEW 0，无 NEEDS-REVIEW case 可供评估幻觉迹象。"}, {"q": "综合以上：本次 sweep 结果整体可信吗？", "answer": "no", "evidence": "Q1 揭示至少 1 个 PASS case（write-template-instantiate）主产物为 1 行占位符，属于病态产物。这表明 verdict 判定逻辑或 artifact 收集机制存在 bug：要么错误地将 stub 判为 PASS，要么 retry 成功后未正确更新主产物。虽然 6/7 case 产物健康且无 SKIP/FAIL/NEEDS-REVIEW，但单个 PASS+stub 案例已损害 sweep 整体可信度。建议检查 write-template-instantiate 的 verdict 判定依据和 retry 机制的 artifact 更新逻辑。"}], "summary": "本次 sweep 存在可信度缺陷：write-template-instantiate 被标记为 PASS 但主产物为 1 行占位符，表明 verdict 或 artifact 收集有 bug；其余 6 个 PASS case 产物健康，无 SKIP/FAIL/NEEDS-REVIEW，但单个病态产物已损害整体可信度。"}
```
