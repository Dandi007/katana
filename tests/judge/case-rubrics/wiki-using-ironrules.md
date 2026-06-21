<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW。 -->
# wiki:using-wiki 铁律注入语义验收

判定 using-wiki skill 注入后，agent 对意式浓缩萃取温度的回答是否遵守 wiki 铁律（引用库内矛盾笔记）。

逐项 yes/no + evidence（引用回答原文行）：
1. 回答是否同时提及 92°C 和 96°C 两个温度值（体现库内矛盾，未武断取一）？
2. 回答是否包含 wikilink 格式引用（形如 `[[...]]`），指向库内真实存在的笔记？
3. 回答是否未凭空合并两个温度为单一结论（保持分歧独立性）？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
