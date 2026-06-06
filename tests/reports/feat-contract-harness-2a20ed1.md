# Contract Sweep Report

- branch: `feat/contract-harness` @ `2a20ed1`
- date: 2026-06-07 02:20
- jobs: 1 / total: 416s
- **PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| wiki:lint#lint-full | PASS | — | 1 | 190s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

All report claims are grounded: the `笔记/` pages, both `raw/espresso-guide-{a,b}.md` sources, and the 92°C/96°C conflict all exist exactly as described. The lint-report is substantive, not boilerplate.

```json
{"items": [{"q": "PASS 但产物病态的 case?", "answer": "no", "evidence": "唯一 PASS 的 wiki:lint#lint-full,其 kb/lint-report.md 是实质内容:机械检查逐项给结论(孤儿/死链/provenance/索引一致性),语义检查点名了真实矛盾对 意式浓缩温度-A(92°C)⟷-B(96°C),并给出不可合并的理由。非空话、非复读 prompt、非占位符。"}, {"q": "SKIP 集中到某 plugin 形成覆盖空洞?", "answer": "no", "evidence": "报告 SKIP=0,Skipped 段为 none。不存在 SKIP,故无集中性空洞。"}, {"q": "FAIL 归因与 detail 自洽?", "answer": "no", "evidence": "报告 FAIL=0,无任何 FAIL case,因此不存在归因-detail 自洽性可评判的对象(N/A,无 FAIL 即无此问题)。"}, {"q": "NEEDS-REVIEW judge 裁决有幻觉?", "answer": "no", "evidence": "NEEDS-REVIEW=0,Overall Verdict 标注 (not run),无 judge 裁决产生。逐一核对报告引用的 espresso-guide-a.md/-b.md、笔记/INDEX.md、A/B 两页均真实存在且内容吻合,无幻觉文件。"}, {"q": "本次 sweep 结果整体可信?", "answer": "yes", "evidence": "唯一 case 断言与产物完全一致且可溯源,wiki-log.md 也记录了 lint 动作。但需提醒:本次仅 1 个 case(全 PASS),覆盖面极窄,可信但不构成对 harness 的充分回归。"}], "summary": "单 case 全绿且产物真实可溯源、无病态无幻觉,结果可信——唯覆盖仅 1 例,样本过小不足以代表整体回归。"}
```
