---
name: official-docs
description: 官方文档检索源（SSoT 级）。Use when 需要官方一手文档/API 参考。WebFetch 抓官方域；可信度 high。
---

# /retrieval:official-docs

主路：`WebFetch` 抓官方文档域（docs.anthropic.com / support.claude.com / 各项目官网）。
可信度：high（一手）。降级走 exa MCP（`web_search_exa`/`web_fetch_exa`，可信度封顶 medium）。
