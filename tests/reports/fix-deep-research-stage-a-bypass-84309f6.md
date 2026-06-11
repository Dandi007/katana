# Contract Sweep Report

- branch: `fix/deep-research-stage-a-bypass` @ `84309f6`
- date: 2026-06-12 01:16
- jobs: 4 / total: 36s
- **PASS 0 / FAIL 0 / SKIP 1 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| deep-research:deep-research#deep-research-kb | SKIP | — | 0 | 0s | — | env KATANA_E2E_NETWORK unset |

## Skipped

- deep-research:deep-research#deep-research-kb: env KATANA_E2E_NETWORK unset

## NEEDS-REVIEW

- none

## Overall Verdict

逐项核对报告数据后:

- 总计仅 1 个 case,结果分布:PASS 0 / FAIL 0 / SKIP 1 / NEEDS-REVIEW 0
- 唯一 case `deep-research:deep-research#deep-research-kb` 因 `KATANA_E2E_NETWORK` 未设置被 SKIP
- Artifact Index 为空,Overall Verdict 标记 `(not run)`

```json
{"items": [
  {"q": "有没有断言全绿(PASS)但产物病态的 case?", "answer": "no", "evidence": "整张表 PASS 0,无任何 PASS case,故不存在'绿但病态'的情况;且 Artifact Index 为空,无产物可检。"},
  {"q": "SKIP 是否集中到某 plugin 形成覆盖空洞?", "answer": "yes", "evidence": "全 sweep 仅 1 个 case,即 deep-research plugin 的 deep-research-kb,且被 SKIP(env KATANA_E2E_NETWORK unset)。该 plugin 唯一用例未执行,等于 deep-research 实测覆盖为零——这是覆盖空洞,只是 N=1 使'集中'退化为'全部'。"},
  {"q": "FAIL case 归因与 detail 是否自洽?", "answer": "no", "evidence": "FAIL 0,无 FAIL case;表中唯一条目是 SKIP,归因列为'—',detail 为'env KATANA_E2E_NETWORK unset',与 SKIP 语义自洽,但不存在可评判的 FAIL 归因。"},
  {"q": "NEEDS-REVIEW judge 裁决有无幻觉引用?", "answer": "no", "evidence": "NEEDS-REVIEW 段落明确为 'none',无 judge 裁决文本,故无幻觉引用迹象。"},
  {"q": "本次 sweep 结果整体可信吗?", "answer": "no", "evidence": "唯一用例 SKIP、Artifact Index 空、Overall Verdict 标记 '(not run)'——sweep 实质未执行任何断言,既无 PASS 也无 FAIL 信号,作为契约回归结果不提供任何可信验证力(非数据造假,而是空跑无信号)。"}
],
"summary": "这是一次空跑:唯一的 deep-research 用例因环境变量未设而 SKIP、verdict 未运行、产物为空,无病态绿/FAIL 归因/judge 幻觉可言,但整体不提供任何回归可信度。"}
```
