# Contract Sweep Report

- branch: `feat/contract-harness` @ `6cd9db4`
- date: 2026-06-07 01:50
- jobs: 4 / total: 426s
- **PASS 5 / FAIL 0 / SKIP 1 / NEEDS-REVIEW 1**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | — | dir missing: $KATANA_TEST_XHS_PROFILE |
| wiki:ingest#ingest-inbox | PASS | — | 1 | 112s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-full | NEEDS-REVIEW | — | 1 | 217s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 58s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 49s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 72s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-save | PASS | — | 1 | 70s | lingzhi/claude-opus-4-8 |  |

## Skipped

- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

### wiki:lint#lint-full
- [yes] 报告点名的矛盾是否就是 92°C/96°C 温度分歧? — 报告 'A asserts 92°C (from raw/espresso-guide-a.md), B asserts 96°C (from raw/espresso-guide-b.md)' — 与 fixture 意式浓缩温度-A.md('最佳萃取水温是 92°C')、意式浓缩温度-B.md('最佳萃取水温是 96°C')完全一致,无幻觉矛盾
- [yes] 报告引用的页面是否全部真实存在于 笔记/ 目录? — 报告引用 [[意式浓缩温度-A]]、[[意式浓缩温度-B]]、[[V60滤杯]]、[[手冲咖啡萃取]];笔记/ 实际含 INDEX/V60滤杯/咖啡豆烘焙度/手冲咖啡萃取/水质对萃取的影响/意式浓缩温度-A/意式浓缩温度-B,全部命中,无幻觉页名
- [no] 报告有没有把矛盾调和成一个结论? — 报告明确 'Do not merge or reconcile — the split + callouts are the deliverable',且 Merge candidates 段 'Explicitly NOT a merge candidate ... Merging them would smooth the conflict and violate the antagonist rule. They stay separate.' 未抹平
- [yes] 孤儿页/provenance 检查结论与库实际状态一致吗? — 报告 'All 6 content pages carry source: frontmatter' 与 'Every content page's source: resolves to a raw/ file' — fixture 中 6 个内容页(V60/咖啡豆烘焙度/手冲/水质/A/B)均有 source:;INDEX.md 链向全部页面故无孤儿,报告将 INDEX 自身 orphan 检查 skipped 处理,与无孤儿设计一致

## Overall Verdict

All artifacts inspected and cross-checked against fixtures. Findings:

- **PASS cases**: every artifact is substantive and grounded. `ingest-inbox` actually created `笔记/冷萃咖啡.md` (real content: 12–18h 浸泡/1:8/酸度低60%), added backlink, archived inbox→raw. `query-hot` cited only real pages. `query-cold` correctly declined and logged the gap. `checkpoint-save/resume` produced real resume guides referencing real files. No empty/echo/placeholder output anywhere.
- **NEEDS-REVIEW judge**: lint report's 92°C/96°C conflict matches `意式浓缩温度-A.md`/`-B.md` verbatim; all cited pages (A/B, V60滤杯, 手冲咖啡萃取) exist in `笔记/`. No hallucinated files.
- **SKIP**: only one, `retrieval:xiaohongshu`, env-gated (missing `$KATANA_TEST_XHS_PROFILE`), no case dir created — legitimate.
- **FAIL**: zero cases.

```json
{"items": [
  {"q": "有没有断言全绿(PASS)但产物内容明显病态的 case?", "answer": "no", "evidence": "逐个核验 5 个 PASS 产物均实质且落盘正确:ingest-inbox 真实新建 笔记/冷萃咖啡.md(浸泡12-18h/粉水比1:8/酸度低约60%+绿原酸解释),并回链 手冲咖啡萃取、归档 inbox/cold-brew-source.md→raw/;query-hot 输出真实参数(90-94°C/1:15)并 Sources 指向真实页;query-cold 正确判 cold path 并写 gap-log.md;checkpoint-save 生成实质 CLAUDE.md/progress.md/findings.md。无空话/复读 prompt/占位符。"},
  {"q": "SKIP 是否集中到某 plugin 形成覆盖空洞?", "answer": "no", "evidence": "全 sweep 仅 1 个 SKIP(retrieval:xiaohongshu#xiaohongshu-download),且因 env 缺失($KATANA_TEST_XHS_PROFILE,无 case 目录生成),属环境门控而非系统性聚集;不存在多个 SKIP 堆到同一 plugin 的'集中'。但需注意:它是本次 retrieval plugin 的唯一 case,故 retrieval 本轮 0 条已执行覆盖,这一点是真实的覆盖薄弱,建议补 env 后回归。"},
  {"q": "FAIL case 归因与 detail 是否自洽?", "answer": "yes", "evidence": "本次 FAIL 0,不存在任何 FAIL case,因此不存在归因与 detail 不自洽的情况(空真命题);无可质疑项。"},
  {"q": "NEEDS-REVIEW judge 裁决有无引用不存在文件/页面的幻觉?", "answer": "no", "evidence": "核验 lint-full judge 四问:报告 92°C/96°C 与 意式浓缩温度-A.md('92°C')、-B.md('96°C')逐字一致;引用页 [[意式浓缩温度-A]]/[[-B]]/[[V60滤杯]]/[[手冲咖啡萃取]] 均实存于 笔记/(8 个文件);'6 content pages carry source:' 与实际 frontmatter 一致。无幻觉文件/页名。"},
  {"q": "本次 sweep 结果整体可信吗?", "answer": "yes", "evidence": "5/5 PASS 产物均经文件级核验为真实且 grounded,NEEDS-REVIEW judge 裁决全部可复现无幻觉,唯一 SKIP 为合法 env 门控,FAIL 0。唯二保留:(a) retrieval plugin 本轮 0 已执行覆盖;(b) 报告 Overall Verdict 标注 'not run',即综合裁决此前未自动跑——本次为人工补齐。"}
],
"summary": "结果整体可信:5 个 PASS 产物均实质且与 fixture 完全 grounded、NEEDS-REVIEW judge 无幻觉、唯一 SKIP 为合法 env 门控、FAIL 为 0;唯一需补的是 retrieval plugin 本轮零执行覆盖(env 缺失所致)与 Overall Verdict 原本未自动运行。"}
```
