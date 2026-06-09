# Contract Sweep Report

- branch: `chore/wiki-v0.2.0` @ `6b8fa45`
- date: 2026-06-10 00:30
- jobs: 4 / total: 201s
- **PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| wiki:lint#lint-summary-backfill | PASS | — | 1 | 77s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

I've verified the actual artifacts. Findings:

- The single PASS case `lint-summary-backfill` produced real, content-grounded summaries (e.g. 手冲咖啡萃取 → "水温90–94°C、粉水比1:15，烘焙越深水温越低"), each derived from the page body — not placeholders or prompt echoes.
- The golden `.body` files match the produced bodies byte-for-byte, confirming only frontmatter `摘要` was written and正文 untouched.
- The lint report and `log.md` are self-consistent (2 backfilled, V60 skipped as already-non-empty).
- Tallies: PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0; Overall Verdict "(not run)".

```json
{"items": [
  {"q": "有没有 PASS 但产物病态的 case?", "answer": "no", "evidence": "唯一 PASS 的 lint-summary-backfill,其 kb-summary/笔记/手冲咖啡萃取.md、咖啡豆烘焙度.md 的『摘要』均为正文实义提炼(如『水温90–94°C、粉水比1:15』),非空话/复读/占位符;且 .golden/*.body 与产物正文逐字一致,证明只改 frontmatter、正文字节未动。"},
  {"q": "SKIP 是否集中到某 plugin 形成覆盖空洞?", "answer": "no", "evidence": "报告 SKIP 0,Skipped 段为 none;不存在被跳过的 case。(注:全 sweep 仅 1 个 case,覆盖面本身极窄,但无 SKIP 聚集问题。)"},
  {"q": "FAIL 归因与 detail 是否不自洽?", "answer": "no", "evidence": "FAIL 0,无任何 FAIL case,不存在归因/detail 冲突;结果表 FAIL 列为空。"},
  {"q": "NEEDS-REVIEW judge 裁决有无引用不存在文件的幻觉?", "answer": "no", "evidence": "NEEDS-REVIEW 0,该段为 none,Overall Verdict 标注『(not run)』,无 judge 裁决文本可产生幻觉。"},
  {"q": "本次 sweep 整体可信吗?", "answer": "yes", "evidence": "唯一结果经产物核验可信:摘要内容真实、正文与 golden 一致、lint 报告与 log 自洽,无 PASS-病态/SKIP-空洞/FAIL-不自洽/幻觉。唯一保留点是这是单 case sweep,仅覆盖 wiki:lint 一条路径,代表性有限——结果可信但覆盖面薄。"}
],
"summary": "单 case sweep,唯一 PASS 经产物核验为真实非病态、各项异常计数均为 0,结果可信但覆盖面仅限 wiki:lint 一条路径。"}
```
