---
name: memory-shared
description: Internal memory reference holder for Codex plugin packaging. Do not invoke directly; use memory MCP tools (memory_index / memory_get / memory_create / memory_update / memory_delete).
---

# Memory Shared

Internal reference holder for Codex plugin packaging. Memory 卡片生命周期的读写
统一经 `katana-memory-mcp` 的 `memory_*` MCP tools 直用，无用户入口 skill。