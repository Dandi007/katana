---
name: using-wiki
description: Hook-injected convention layer for the wiki plugin. Active whenever a wiki is initialized in this project; governs how to ground answers in the wiki, when to route to /wiki:* skills, and the iron rules that keep provenance and linking intact.
---

# Using Wiki

本项目 wiki 只经 katana-wiki-mcp 访问：知识问题先调 `wiki_query` 再答、勿凭记忆裸答；检索用 `wiki_search`，深挖逻辑路径用 wiki MCP `fs_read` / `fs_glob`；入库用 `wiki_ingest_plan` → `wiki_ingest_apply`。禁止用原生文件工具访问 wiki，物理根不进入 client 认知。完整 invariants 随 `/wiki:*` skill 调用载入。
