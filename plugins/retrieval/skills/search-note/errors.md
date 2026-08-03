# Errors

## 2026-07-10 14:12 — vault-search 服务超时

- 场景：在 `/data/vault` 查询 `work folder wiki MCP 设计` 等 4 组关键词。
- 命令：`POST http://127.0.0.1:18082/search`，timeout 8s。
- 结果：4 个请求均返回 `curl: (28) Operation timed out ... with 0 bytes received`。
- 处置：按 `/retrieval:search-note` fallback 改用 keyword grep + filename glob；降级结果可信度封顶 medium。

