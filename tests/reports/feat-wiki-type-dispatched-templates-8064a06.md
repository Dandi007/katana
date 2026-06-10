# Contract Sweep Report

- branch: `feat/wiki-type-dispatched-templates` @ `8064a06`
- date: 2026-06-10 20:17
- jobs: 4 / total: 698s
- **PASS 7 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| wiki:ingest#ingest-inbox | PASS | — | 1 | 93s | lingzhi/claude-opus-4-8 |  |
| wiki:init#init-adopt | PASS | — | 1 | 92s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-full | PASS | — | 1 | 294s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-summary-backfill | PASS | — | 1 | 88s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 76s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 47s | lingzhi/claude-opus-4-8 |  |
| wiki:using-wiki#using-wiki-ironrules | PASS | — | 1 | 48s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

All seven PASS cases inspected against their on-disk artifacts. Findings:

- **ingest-inbox**: real ingested notes, conflict pair (92°C/96°C) correctly cross-linked not flattened, wiki-log records the action, INDEX bidirectional.
- **init-adopt**: induced a genuine `pages` schema into `init-arena/` with coherent WIKI.md/index.md/log.md scaffold matching the narration.
- **lint-full / lint-summary-backfill**: substantive reports; backfill's produced body matches `.golden/*.body` byte-for-byte (only `摘要` frontmatter added).
- **query-cold**: correct gap-log entry, refused to pass off training knowledge as wiki content. **query-hot**: grounded answer with real `[[wikilink]]` citations.
- **using-wiki-ironrules**: conflict surfaced both-sided, inference explicitly tagged `[inference]`.

The report itself records SKIP 0 / FAIL 0 / NEEDS-REVIEW 0, so Q2–Q4 have no instances to be pathological about.

```json
{"items": [
{"q": "有没有断言全绿(PASS)但产物内容明显病态的 case(空话文件、复读 prompt、占位符正文)?", "answer": "no", "evidence": "逐一核验 7 个 PASS case 的实际产物均为真实内容:ingest-inbox/笔记/手冲咖啡萃取.md 含具体参数(90–94°C、1:15)与互链;init-adopt 在 init-arena/ 生成完整 WIKI.md schema 与 index.md;lint-full/lint-report.md 含非平凡机械+语义+治理三段分析;query-hot 给出带 [[手冲咖啡萃取]] 等真实引用的回答。无空话/复读/占位符。"},
{"q": "SKIP 的 case 是否集中到了某个 plugin,形成覆盖空洞?", "answer": "no", "evidence": "报告 'Skipped' 段为 none,SKIP=0,7 个 case 覆盖 ingest/init/lint/query/using-wiki 全部 skill,无空洞。"},
{"q": "FAIL case 的归因标注与 detail 是否自洽?", "answer": "yes", "evidence": "FAIL=0,无 FAIL case;表中所有行 result=PASS、归因列为 '—'、detail 为空,不存在归因/detail 冲突。(无样本,空真)"},
{"q": "NEEDS-REVIEW 的 judge 裁决里有没有引用不存在文件/页面的幻觉迹象?", "answer": "no", "evidence": "NEEDS-REVIEW=0,'NEEDS-REVIEW' 段为 none,Overall Verdict 标注 '(not run)',无任何 judge 裁决文本可产生幻觉。"},
{"q": "综合以上:本次 sweep 结果整体可信吗?", "answer": "yes", "evidence": "7/7 PASS 的产物经文件级核验均真实且符合契约——尤其 summary-backfill 的产出正文与 .golden/*.body 完全一致(仅加 frontmatter)、冲突对两案均正确命名互链未抹平、query-cold 正确写 gap-log 拒绝幻答;无病态/空洞/幻觉。可信。"}
],
"summary": "7/7 PASS 经产物级核验全部为真实且合契约的内容,无病态绿、无 SKIP 空洞、无 FAIL 归因冲突、无 judge 幻觉——本次 sweep 整体可信。"}
```
