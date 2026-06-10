# Contract Sweep Report

- branch: `feat/incubate` @ `ab951de`
- date: 2026-06-10 14:21
- jobs: 1 / total: 339s
- **PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| incubate:incubate#incubate-e2e | PASS | — | 1 | 249s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

I've verified the single PASS case's artifacts against its verify script. Findings:

**The verify script** (`incubate-e2e.verify.sh`) checks: README exists + contains "已毕业", golden-order has idea keywords, findings has source markers, spec.md is non-empty.

**The actual artifacts** are substantive and internally coherent, not pathological:
- `spec.md` (2773 bytes) is a real synthesized doc with source-tagging (📄/💭/🔗), a source-mapping table, and even honestly flags a dangling link (`[[机器人工程闭环...综述]]` not yet in kb) as an open question.
- `golden-order.md` preserves the human's original phrasing verbatim.
- `findings.md` cites a source with a credibility rating and flags the same dangling link consistently.
- `README.md` marks 已毕业 with a graduation timestamp and points to spec.md as downstream output.

No reused/copied prompt text, no placeholder bodies, no empty-but-green file. The cross-file story (gather → capture → synthesize → graduate) is consistent.

Q2–Q4 are vacuously "no": the sweep has **0 SKIP / 0 FAIL / 0 NEEDS-REVIEW** — there are no cases of those kinds to exhibit the pathologies asked about.

The one real caveat for Q5: this is a **single-case, single-job** sweep, and the report's "Overall Verdict" section is `_(not run)_`. The one result is trustworthy, but it's far too thin to call a meaningful regression sweep — there's no coverage breadth to judge.

```json
{"items": [
  {"q": "断言全绿但产物病态的 case?", "answer": "no", "evidence": "唯一 PASS 案 incubate-e2e 的产物经核验为实质内容:spec.md(2773B)含来源标注(📄/💭/🔗)+来源映射表,并诚实标注 dangling link [[机器人工程闭环...综述]] 为开放问题;golden-order.md 保留人类原话;findings.md 带可信度评级与一致的 dangling-link 标注。非空话/复读/占位符。"},
  {"q": "SKIP 集中到某 plugin 形成覆盖空洞?", "answer": "no", "evidence": "报告 SKIP=0,'## Skipped' 节为 'none',不存在 SKIP。"},
  {"q": "FAIL 归因与 detail 不自洽?", "answer": "no", "evidence": "报告 FAIL=0,无任何 FAIL 行与归因(归因列为 '—'),无从产生不自洽。"},
  {"q": "NEEDS-REVIEW judge 裁决有幻觉?", "answer": "no", "evidence": "NEEDS-REVIEW=0,'## NEEDS-REVIEW' 节为 'none',无 judge 裁决文本可供检验幻觉。"},
  {"q": "本次 sweep 结果整体可信?", "answer": "yes", "evidence": "唯一结果与产物经 verify.sh 校验项逐条对得上且内容真实,可信;但仅 1 case/1 job、'Overall Verdict' 为 _(not run)_,覆盖面极薄,作为'回归 sweep'的代表性不足。"}
],
"summary": "唯一 PASS 案产物真实自洽、断言可信,SKIP/FAIL/NEEDS-REVIEW 均为 0 故 Q2–Q4 无对象;但仅 1 case 且 Overall Verdict 未跑,结果可信却覆盖面过薄。"}
```
