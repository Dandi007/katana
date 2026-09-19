<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW；只回答下列各项，不新增判据。 -->
# retrieval:using-retrieval — using-retrieval-loader 语义验收

skill 被要求列出 `using-retrieval` 加载器注入的检索约定。逐项 yes/no + evidence（引用回答原文行）：

1. 回答是否提及回答事实性问题前先路由 / 查询信息源？
2. 回答是否提及检索结论要附可信度标注（`high` / `medium` / `low` 或「可信度」）？
3. 回答是否反映该 skill 自身的约定，而非泛泛的通用建议？

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
