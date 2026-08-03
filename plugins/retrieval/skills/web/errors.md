# Errors

## 2026-07-10 14:19 — 原生 WebFetch 拒绝 viz 直链

- 场景：读取 `viz.qinglinzhang.top/2026/07/{01,03,07}/...html` 三个项目可视化页面。
- 结果：`web open` 全部返回 `URL ... is not safe to open (non-retryable error)`。
- 判断：工具 URL 安全门拒绝直开，不等价于站点不可达。
- 处置：先用 domain search 建立可打开结果；仍失败则按 `/retrieval:web` fallback 改用 Exa 或本机只读 HTTP/browser 核验，可信度封顶 medium。
