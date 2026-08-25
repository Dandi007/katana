---
name: official-docs
description: 官方文档检索源（SSoT 级）。Use when 需要官方一手文档/API 参考。WebFetch 抓官方域；可信度 high。
---

# /retrieval:official-docs

主路：`WebFetch` 抓官方文档域（Anthropic API/平台文档 canonical 为 platform.claude.com/docs，Claude Code 文档为 code.claude.com/docs；docs.anthropic.com 与 docs.claude.com 已 301/302 跳转，直接写 canonical URL 省一跳。support.claude.com / 各项目官网照旧）。
可信度：high（一手）。降级走 exa MCP（`web_search_exa`/`web_fetch_exa`，可信度封顶 medium）。
