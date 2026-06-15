---
name: using-wiki
description: Hook-injected convention layer for the wiki plugin. Active whenever a wiki is initialized in this project; governs how to ground answers in the wiki, when to route to /wiki:* skills, and the iron rules that keep provenance and linking intact.
---

# Using Wiki

本项目有 wiki（root `{{WIKI_ROOT}}`，schema `{{WIKI_ROOT}}/WIKI.md`）。知识问题先经 `/wiki:query` 再答、勿凭记忆裸答，每条结论带 citation（无则标为推断）；写库只走 `/wiki:ingest`（直接 Write 是反模式），raw 不可变（model-collapse 防御）。`/wiki:init` 须全限定以避开内置 `/init`。路由（query/ingest/init/lint）与完整 invariants 随 `/wiki:*` 调用载入。
