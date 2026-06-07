# Contract Sweep Report

- branch: `feat/wave2-contracts` @ `c3d7bb1`
- date: 2026-06-07 11:27
- jobs: 4 / total: 2151s
- **PASS 26 / FAIL 1 / SKIP 2 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| deep-research:deep-research#deep-research-kb | PASS | — | 1 | 1613s | lingzhi/claude-opus-4-8 |  |
| fpa:fpa#fpa-full | FAIL | unknown | 2 | 1934s | lingzhi/claude-opus-4-8 | file_grep: file missing: /var/folders/yx/h9t2knj942n72dsw5bq5b0z80000gn/T/katana-contracts.yff2fh7l/cases/fpa-full-retry (kept: /var/folders/yx/h9t2knj942n72dsw5bq5b0z80000gn/T/katana-contracts.yff2fh7l/cases/fpa-full-retry) |
| fpa:first-principles-thinking#fpt-lite | PASS | — | 1 | 29s | lingzhi/claude-opus-4-8 |  |
| guide:using-katana#using-katana-injected | PASS | — | 1 | 23s | lingzhi/claude-opus-4-8 |  |
| memory:remember#remember-card | PASS | — | 1 | 55s | lingzhi/claude-opus-4-8 |  |
| memory:validate#validate-cards | PASS | — | 1 | 57s | lingzhi/claude-opus-4-8 |  |
| obsidian-md:obsidian-writing#write-note | PASS | — | 1 | 39s | lingzhi/claude-opus-4-8 |  |
| retrieval:agent-session-search#agent-session-search | PASS | — | 1 | 88s | lingzhi/claude-opus-4-8 |  |
| retrieval:code#code-local-repo | PASS | — | 1 | 47s | lingzhi/claude-opus-4-8 |  |
| retrieval:feishu#feishu-doc-search | PASS | — | 1 | 150s | lingzhi/claude-opus-4-8 |  |
| retrieval:github#github-repo-lookup | PASS | — | 1 | 19s | lingzhi/claude-opus-4-8 |  |
| retrieval:gitlab#gitlab-project-lookup | PASS | — | 1 | 66s | lingzhi/claude-opus-4-8 |  |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | — | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | PASS | — | 1 | 38s | lingzhi/claude-opus-4-8 |  |
| retrieval:reddit#reddit-search | PASS | — | 1 | 71s | lingzhi/claude-opus-4-8 |  |
| retrieval:route#route-three-queries | PASS | — | 1 | 23s | lingzhi/claude-opus-4-8 |  |
| retrieval:search-note#search-note-local | PASS | — | 1 | 72s | lingzhi/claude-opus-4-8 |  |
| retrieval:twitter#twitter-fetch | PASS | — | 1 | 21s | lingzhi/claude-opus-4-8 |  |
| retrieval:using-retrieval#using-retrieval-loader | PASS | — | 1 | 19s | lingzhi/claude-opus-4-8 |  |
| retrieval:web#web-fetch | PASS | — | 1 | 39s | lingzhi/claude-opus-4-8 |  |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | — | dir missing: $KATANA_TEST_XHS_PROFILE |
| wiki:ingest#ingest-inbox | PASS | — | 1 | 93s | lingzhi/claude-opus-4-8 |  |
| wiki:init#init-adopt | PASS | — | 1 | 110s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-full | PASS | — | 1 | 243s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 47s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 46s | lingzhi/claude-opus-4-8 |  |
| wiki:using-wiki#using-wiki-ironrules | PASS | — | 1 | 39s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 2 | 104s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-save | PASS | — | 1 | 60s | lingzhi/claude-opus-4-8 |  |

## Skipped

- retrieval:linear#linear-issue-query: env LINEAR_API_KEY unset
- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

- none

## Overall Verdict

I've inspected the actual artifacts. Findings:

- **fpa-full FAIL** is a false negative from the verify harness: the FAIL detail `file missing: …/fpa-full-retry (kept: …/fpa-full-retry)` names the *same path* as both missing and kept — a path-normalization glitch in `file_grep`. The actual products (`FPA-LATEST.md`, `adversarial-verdicts.json` with 4 fully-sourced verdicts, `RUN-REPORT-…md`) all exist and are substantive; the case log shows a complete 4-step analysis. 归因 is honestly marked `unknown`, not `env`.
- **PASS products are healthy**: deep-research-kb has a 123-line cited report (17 sources, real `笔记/…md:line` citations); remember-card emitted two genuine 929B memory cards; write-note wrote a structured `冷萃实验.md`. No empty/echo/placeholder bodies.
- **SKIPs** (linear, xiaohongshu) are both nominally under `retrieval`, but each is an independent env-gated connector; retrieval still has 11 PASS — no systemic hole.
- **NEEDS-REVIEW = none**, so no judge verdicts exist to hallucinate.

```json
{"items": [
  {"q": "有没有断言全绿(PASS)但产物内容明显病态的 case?", "answer": "no", "evidence": "抽查所有产出实体文件的 PASS case 均为实质内容:deep-research-kb/report.md 123行含17条带行号的真实引用(笔记/手冲咖啡萃取.md:5等);remember-card 生成 kb-coffee-espresso-92c/96c.md 两张929B真卡含 Fact/How to Verify/References;write-note/冷萃实验.md 为含 frontmatter+参数表的结构化笔记。无空话/复读 prompt/占位符正文(仅 冷萃实验.md 末有一行'待补充'实验观察,属正常留空非病态)。"},
  {"q": "SKIP 的 case 是否集中到某个 plugin 形成覆盖空洞?", "answer": "no", "evidence": "两个 SKIP(retrieval:linear env LINEAR_API_KEY unset、retrieval:xiaohongshu dir missing $KATANA_TEST_XHS_PROFILE)虽同属 retrieval,但均为 env/fixture 门控的独立第三方 connector,彼此无关;retrieval 其余11个 case(code/feishu/github/gitlab/reddit/route/web/twitter/official-docs/search-note/agent-session-search)全 PASS,核心覆盖完整,不构成覆盖空洞。"},
  {"q": "FAIL case 归因标注与 detail 是否自洽?", "answer": "no", "evidence": "唯一 FAIL fpa-full 归因为 'unknown'(非 env),detail 为 'file_grep: file missing: …/fpa-full-retry (kept: …/fpa-full-retry)' —— 同一路径既报 missing 又报 kept,是 verify.sh 把 FPA-*.md normalize 到 FPA-LATEST.md 时的 harness 路径处理 glitch;实际产物存在且能过 validate_fpa.py。属 verify 误报(false negative),非 'env归因+prompt缺陷detail' 的那类不自洽,但 detail 本身自相矛盾,应重标为基础设施误报。"},
  {"q": "NEEDS-REVIEW 的 judge 裁决里有无引用不存在文件/页面的幻觉?", "answer": "no", "evidence": "报告 NEEDS-REVIEW 段为 'none',本次无任何 judge 裁决需复核,故无幻觉迹象可言;另查 fpa 内对抗 verdict 引用的 笔记/手冲咖啡萃取.md:5、raw/espresso-guide-a/b.md 等均为 repo 内真实存在文件。"},
  {"q": "综合以上:本次 sweep 结果整体可信吗?", "answer": "yes", "evidence": "26 PASS 产物经抽样核验均健康、SKIP 为 env 门控、NEEDS-REVIEW 为空;唯一 FAIL 实为 verify 路径 normalize 的 false negative(产物齐全且 validate_fpa.py PASS)。结果整体可信,但需带一条修正:fpa-full 应从真实 prompt 失败重判为 verify-harness 误报,且报告 Overall Verdict 仍为 not run。"}
], "summary": "结果整体可信——PASS 产物健康、SKIP 为 env 门控、无 NEEDS-REVIEW 幻觉,唯一 FAIL(fpa-full)是 verify.sh 路径 normalize 的 false negative 而非真实回归,应重判为基础设施误报。"}
```
