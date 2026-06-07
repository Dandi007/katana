# Contract Sweep Report

- branch: `feat/contract-harness` @ `be56dc9`
- date: 2026-06-07 02:35
- jobs: 4 / total: 589s
- **PASS 6 / FAIL 0 / SKIP 1 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | — | dir missing: $KATANA_TEST_XHS_PROFILE |
| wiki:ingest#ingest-inbox | PASS | — | 1 | 132s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-full | PASS | — | 1 | 194s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 58s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 46s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 62s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-save | PASS | — | 1 | 93s | lingzhi/claude-opus-4-8 |  |

## Skipped

- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

- none

## Overall Verdict

I've inspected every PASS case's actual artifacts and independently verified the lint report's claims against the on-disk fixtures. Here's my verdict.

```json
{"items": [
{"q": "有没有断言全绿(PASS)但产物内容明显病态的 case?", "answer": "no", "evidence": "逐个核查 6 个 PASS 产物均为实质内容、非病态: ingest-inbox 生成的 笔记/冷萃咖啡.md 含合成正文(12-18h/1:8/酸度低60%)+ 与[[手冲咖啡萃取]]互链 + INDEX 登记 + inbox→raw 归档; lint-full/lint-report.md 为详尽分项报告; query-hot/case.log 给出带[[..]]引用的扎实答案; query-cold/case.log 正确拒答域外问题并记 gap; checkpoint-save 五个控制文件(CLAUDE/progress/findings/context/golden-order)齐全且互相自洽; checkpoint-resume 做了真实环境核查。无空话/复读 prompt/占位符。"},
{"q": "SKIP 是否集中到某 plugin 形成覆盖空洞?", "answer": "yes", "evidence": "唯一的 SKIP(retrieval:xiaohongshu#xiaohongshu-download, 因 $KATANA_TEST_XHS_PROFILE 目录缺失)恰好是本次 sweep 中 retrieval plugin 的唯一 case —— 即 retrieval 整个 plugin 本轮零执行覆盖, wiki(4)与 work-folder(2)各有 PASS 兜底, 唯独 retrieval 全空。虽只 1 条 SKIP 谈不上'多条堆积', 但落点使该 plugin 形成真实盲区。"},
{"q": "FAIL case 归因标注与 detail 是否自洽?", "answer": "no", "evidence": "本次 FAIL 0(报告头 'PASS 6 / FAIL 0'), 无任何 FAIL case, 故不存在归因与 detail 不自洽的情形 —— 空集, 无不自洽。"},
{"q": "NEEDS-REVIEW judge 裁决里有无幻觉迹象?", "answer": "no", "evidence": "NEEDS-REVIEW 段为 'none', Overall Verdict 标注 '(not run)', 本轮无任何 judge 裁决文本, 因此无引用不存在文件/页面的幻觉。另外我反向核查了 lint 报告自身的引用: 烘焙度 provenance 缺陷(页面浅焙90-92°C vs 源 raw/coffee-basics.md 92-94°C)、A/B 温度冲突(92°C/96°C 互链)均与磁盘真实一致, 无幻觉。"},
{"q": "综合: 本次 sweep 结果整体可信吗?", "answer": "yes", "evidence": "6 个 PASS 产物全部实质且忠实于 fixture; lint 报告的关键论断经我对照 raw 源独立复核为真; query 冷/热路径分别正确触发 gap 与扎实引用作答; checkpoint 存/取闭环自洽。唯二保留: (1) retrieval plugin 本轮零执行覆盖(env fixture 缺失), (2) Overall Verdict 段 not run。两者不影响已执行部分的可信度, 但属覆盖完整性缺口。"}
],
"summary": "已执行的 6 个 PASS 产物经独立核验全部真实可信、无病态无幻觉、归因无矛盾; 唯一缺口是 retrieval plugin 因 env fixture 缺失而零覆盖, 整体结论可信但覆盖不完整。"}
```
