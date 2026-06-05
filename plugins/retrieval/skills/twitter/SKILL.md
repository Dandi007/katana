---
name: twitter
description: Twitter/X 检索源。主路：fxtwitter JSON API（公开推/article，无需登录）；降级：Playwright 登录态接管隔离 profile（搜索 timeline / Community Note / 登录墙后内容）。
---

# /retrieval:twitter

## 主路：fxtwitter JSON API（公开内容，无需登录）

对任意公开推文或 X long-form article，优先走 fxtwitter：

```bash
# 带用户名（推荐，更稳）
curl -sL "https://api.fxtwitter.com/<screen_name>/status/<tweet_id>"

# 不带用户名
curl -sL "https://api.fxtwitter.com/status/<tweet_id>"
```

### 返回 JSON 字段路径（已验证）

| 字段 | 路径 | 说明 |
|------|------|------|
| 推文正文 | `.tweet.text` | 普通推文正文；article 类型可能为空 |
| 展开正文（含 facets） | `.tweet.raw_text.text` | 字符串，facets 在 `.tweet.raw_text.facets` |
| 作者 screen_name | `.tweet.author.screen_name` | |
| 作者显示名 | `.tweet.author.name` | |
| 发布时间 | `.tweet.created_at` | RFC 2822 字符串，如 `Wed Jun 03 10:14:47 +0000 2026` |
| 时间戳 | `.tweet.created_timestamp` | Unix 秒 |
| Long-form article 标题 | `.tweet.article.title` | article 类型才有 |
| Long-form article 预览 | `.tweet.article.preview_text` | |
| Long-form article 正文块 | `.tweet.article.content.blocks[]` | Draft.js 结构，每块 `.text` 字段为纯文本段落 |
| Community Note | `.tweet.community_note` | 若有社区备注则非 null |

**注意**：article 类型推文的 `.tweet.text` 通常为空字符串，正文在 `.tweet.article` 里；`.tweet.raw_text` 是 object（含 `.text`），不是字符串。

### 使用示例

```bash
# 抓推文并提取核心字段
curl -sL "https://api.fxtwitter.com/0xEcho99/status/2062115773775229318" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
t = d['tweet']
print('author:', t['author']['screen_name'], '/', t['author']['name'])
print('created_at:', t['created_at'])
print('text:', t['text'] or t['raw_text']['text'])
if t.get('article'):
    print('article title:', t['article']['title'])
    blocks = t['article']['content']['blocks']
    print('article body:', '\n'.join(b['text'] for b in blocks if b['text']))
"
```

## 降级：Playwright 登录态接管（登录墙后内容）

以下情况走 Playwright MCP（`mcp__playwright__*`）隔离 profile（`twitter_chrome_profile`）：

- 搜索 timeline：`x.com/search?q=...&f=live`
- 需登录态的私有/受保护账户
- Community Note 原始内容（虽 fxtwitter 已返回 `.community_note`，但需上下文时）
- fxtwitter 返回 `{"code": 404}` 或 `{"code": 500}` 时

**降级操作**：`browser_navigate` → 目标 URL → `browser_evaluate` 取
`article[data-testid="tweet"]` 的 `tweetText` / `User-Name` / `time` / `birdwatch-pivot`(Community Note)。

登录一次人工扫码，登录态长期留 profile。详见 memory `playwright-mcp-browser-takeover`。

配置：`twitter_chrome_profile`（.katana；登录态在目录内，非密钥入 repo）。
