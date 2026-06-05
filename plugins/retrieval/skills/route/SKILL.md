---
name: route
description: 检索编排契约——按 query intent 路由到正确信息源，应用可信度阶梯与降级链。内部依赖，consumer skill（deep-research/explain/verify）引用。
---

# /retrieval:route

内部编排契约：怎么找到信息。

1. 解析 intent → query type
2. 按 references/routing.md 选 target sources
3. 每个 source 调 `/retrieval:<source>`
4. 应用 references/credibility.md 评级；主路失败走 references/fallback.md（降级结果封顶 medium）
5. 返回各源结果 + 可信度标注

启用的源由 `.katana` 的 `retrieval_sources` 决定。
