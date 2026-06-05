---
name: web
description: Web 检索源。Use when 需要公开网络信息、官方文档、新闻。主路 WebFetch/WebSearch；失败降级 exa MCP，再降 r.jina.ai。
---

# /retrieval:web

主路：原生 `WebFetch` / `WebSearch`。
降级 1：`mcp__*exa*__web_search_exa` / `web_fetch_exa`（reddit/被墙站优先直接用 exa）。
降级 2：`curl <web_proxy> https://r.jina.ai/<URL>`（reader 代理）。

配置：`web_proxy`（.katana）。降级结果可信度封顶 medium（见 _shared/credibility）。
