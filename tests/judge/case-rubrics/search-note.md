<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# Rubric: retrieval:search-note 搜索结果语义验收

判定搜索结果文件是否正确命中了意式浓缩温度相关的 A/B 两派笔记。

逐项 yes/no + evidence（引用 search-result.md 相关行）：
1. search-result.md 中是否列出了「意式浓缩温度-A」（或等价笔记名）？
2. search-result.md 中是否列出了「意式浓缩温度-B」（或等价笔记名）？
3. 结果是否同时包含 A 和 B 两条笔记（即两派温度主张均被检出，未遗漏任一）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
