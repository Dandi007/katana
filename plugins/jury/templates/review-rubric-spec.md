# 代码评审 rubric（spec 符合性版，jury 验收委员会）

下面给你 **spec（评审目标）+ git diff + 改动所在 worktree**（你可用 Read/Grep/Glob 翻看周边代码）。请**独立评审**，不要假设其他评审者的存在。

逐项回答 yes/no + evidence（引用具体 spec 条目、文件:行或 diff 片段），**yes = 好/符合**：

1. 改动是否**实现了 spec 要求**的目标（对照 spec 关键要求逐一确认）？
2. 是否**未偏离 spec**——没做 spec 没要求、或与 spec 相悖的事？
3. 是否**无多做（YAGNI）**——没有 spec 之外的投机扩展？
4. 测试是否覆盖了 **spec 描述的行为**（非永真、非空、未删失败用例）？

先输出 fenced json：
```json
{"items":[{"q":"1","answer":"yes|no","evidence":"..."},{"q":"2","answer":"yes|no","evidence":"..."},{"q":"3","answer":"yes|no","evidence":"..."},{"q":"4","answer":"yes|no","evidence":"..."}]}
```
再用散文补充你认为最关键的 1–3 条评审意见（可与上面重复展开）。
