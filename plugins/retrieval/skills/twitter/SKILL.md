---
name: twitter
description: Twitter/X 检索源。无公开 API；用 Playwright 登录态接管隔离 profile 读公开推/搜索/Community Note。
---

# /retrieval:twitter

主路：Playwright MCP（`mcp__playwright__*`）隔离 profile（`twitter_chrome_profile`）登录态接管：
`browser_navigate` 到 `x.com/search?q=...&f=live` 或 `x.com/<user>/status/<id>` → `browser_evaluate` 取
`article[data-testid="tweet"]` 的 `tweetText` / `User-Name` / `time` / `birdwatch-pivot`(Community Note)。
登录一次人工扫码，登录态长期留 profile。详见 memory `playwright-mcp-browser-takeover`。

配置：`twitter_chrome_profile`（.katana；登录态在目录内，非密钥入 repo）。
