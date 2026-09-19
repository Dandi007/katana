<!-- 规约：所有问题必须正极性——yes=符合预期。judge 对任何非 yes 记 NEEDS-REVIEW；只回答下列各项，不新增判据。 -->
# retrieval:route — route-three-queries 语义验收

skill 被要求把 3 个 query 路由到正确的信息源（无需真实检索）。逐项 yes/no + evidence（引用回答原文行）：

1. Query 1（RX 7900 XTX 显卡 reddit 评价）是否被明确路由到 `reddit` / `retrieval:reddit`？
2. Query 2（React useEffect 官方文档）是否被明确路由到 `official-docs` / `retrieval:official-docs`？
3. Query 3（本地笔记里关于意式浓缩）是否被明确路由到 `search-note` / `retrieval:search-note`？

每条路由须清楚命名而非暗示；回答中附带的降级说明或执行备注不影响以上判定。

输出 fenced json：{"items": [{"q": "...", "answer": "yes|no", "evidence": "..."}]}
