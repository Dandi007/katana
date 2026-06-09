# Contract Sweep Report

- branch: `feat/wiki-summary-index` @ `bd02d27`
- date: 2026-06-10 00:12
- jobs: 4 / total: 188s
- **PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| wiki:lint#lint-summary-backfill | PASS | — | 1 | 88s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

I've verified the artifacts. The single PASS case is genuine: the two backfilled summaries are concise, on-topic, page-specific (≤40 chars each), bodies are byte-identical to the `.golden/*.body` fixtures (no rewriting), and `log.md` records the action. No placeholder/echo/empty content.

```json
{"items": [{"q": "有断言全绿但产物病态的 case?", "answer": "no", "evidence": "唯一 PASS case lint-summary-backfill 的 kb-summary/笔记/{咖啡豆烘焙度,手冲咖啡萃取}.md 补入的摘要为真实、贴题、页面自身的内容（'烘焙越深越苦、酸度越低，萃取水温需相应下调' 等），正文与 .golden/*.body 逐字一致、未改写，非空话/复读/占位符"}, {"q": "SKIP 集中到某 plugin 形成覆盖空洞?", "answer": "no", "evidence": "报告 SKIP 0,Skipped 段为 'none';本次仅 1 个 case 全部命中 wiki plugin,无 SKIP"}, {"q": "FAIL 归因与 detail 是否不自洽?", "answer": "no", "evidence": "FAIL 0,表中无 FAIL 行,detail 列空,不存在归因/detail 冲突(空洞地成立)"}, {"q": "NEEDS-REVIEW judge 裁决有幻觉引用?", "answer": "no", "evidence": "NEEDS-REVIEW 0,Overall Verdict 标注 '(not run)',无 judge 裁决文本可供产生幻觉"}, {"q": "本次 sweep 整体可信吗?", "answer": "yes", "evidence": "唯一 case 的产物经核验真实可信、与 golden 对齐;但样本极小(1 case/1 plugin)且 Overall Verdict 未运行,可信但覆盖面非常窄,不足以代表全量回归"}], "summary": "唯一 PASS case 产物真实且与 golden 对齐、无病态/SKIP/FAIL/幻觉,结果可信——但这是单 case 单 plugin 的极小 sweep 且总裁决未运行,可信度仅限其覆盖范围。"}
```
