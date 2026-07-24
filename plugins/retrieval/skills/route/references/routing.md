# 信息检索路由表

按查询意图路由到默认信息源。这是路由数据的唯一维护处。

## 意图 → 信息源

| 查询意图 | 默认信息源 |
|---------|-----------|
| 事实核查（机器、IP、凭证、端点） | katana-memory-mcp（`memory_index` L1 已随 SessionStart 注入，全文 `memory_get(id)`） |
| 知识查找（概念、笔记、工作文档） | local_text, web |
| 代码求真（入口、调用链、配置） | code, github, gitlab |
| 平台搜索（飞书文档、issue、MR） | feishu, github, gitlab, linear |
| 网络发现（公开信息、官方文档） | web, reddit, twitter, official-docs |
| 中文消费决策/生活方式调研（口碑、测评、避雷） | xiaohongshu, web |
| 跨源搜索（不确定来源） | local_text, web, code |

## 信息源 → 入口

| 信息源 | 入口 |
|-------|------|
| local_text | `/retrieval:search-note` |
| code | `/retrieval:code` |
| web | `/retrieval:web` |
| feishu | `/retrieval:feishu` |
| github | `/retrieval:github` |
| gitlab | `/retrieval:gitlab` |
| agent_session | `session-engine` MCP（`list_sessions`/`list_events`/`get_event_content`，skill 已退役） |
| reddit | `/retrieval:reddit` |
| twitter | `/retrieval:twitter` |
| official-docs | `/retrieval:official-docs` |
| linear | `/retrieval:linear` |
| xiaohongshu | `/retrieval:xiaohongshu` |
