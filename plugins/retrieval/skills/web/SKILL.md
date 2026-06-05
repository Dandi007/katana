---
name: web
description: Web 检索源。Use when 需要公开网络信息、官方文档、新闻。主路 WebFetch/WebSearch；JS 重/被墙站用 exa MCP。
---

# /retrieval:web

主路：原生 `WebFetch` / `WebSearch`。
peer best-practice：`mcp__*exa*__web_search_exa` / `web_fetch_exa`（reddit/被墙/JS 重站优先直接用 exa）。

配置：`web_proxy`（.katana）。exa 结果可信度封顶 medium（见 _shared/credibility）。
