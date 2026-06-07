# Overall Sweep Verdict Rubric

你将看到一次契约回归 sweep 的完整报告与各 case 产物目录索引。逐项回答 yes/no 并给出 evidence（引用具体 case 或文件）：

1. 有没有断言全绿（PASS）但产物内容明显病态的 case（空话文件、复读 prompt、占位符正文）？
2. SKIP 的 case 是否集中到了某个 plugin，形成覆盖空洞？
3. FAIL case 的归因标注与 detail 是否自洽（归因 env 但 detail 像 prompt 缺陷，属于不自洽）？
4. NEEDS-REVIEW 的 judge 裁决里有没有引用不存在文件/页面的幻觉迹象？
5. 综合以上：本次 sweep 结果整体可信吗？

输出格式（务必 fenced json）：

```json
{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}], "summary": "一句话"}
```
