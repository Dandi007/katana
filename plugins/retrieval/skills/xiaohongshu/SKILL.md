---
name: xiaohongshu
description: 小红书检索源。中文 UGC 消费决策/生活方式调研（餐厅、商品、本地服务的真实口碑）。Playwright 登录态隔离 profile；支持搜索、笔记详情+评论提取、批量下载落盘。
---

# /retrieval:xiaohongshu

小红书无面向个人的开放 API，全程走 Playwright MCP（`mcp__playwright__*`）+ 登录态隔离 profile。

## 配置（.katana）

| 键 | 含义 | 示例 |
|----|------|------|
| `xiaohongshu_chrome_profile` | 登录态 profile 目录 | `~/.playwright-agent-profile` |
| `xiaohongshu_raw_dir` | 下载落盘根目录（相对路径时基于项目根，即 `.katana` 所在目录；与 `kb_dir` 同语义） | `转换文档/web` |

铁律：`.katana` 只放路径；账号与登录态留在 profile 目录内，绝不进 repo。

## 前置：登录态

- **已登录判定**：`browser_navigate` 到 `https://www.xiaohongshu.com/explore`，页面含「消息」「我」且无登录按钮/二维码弹窗。
- **未登录 → 登录引导**：
  1. `/explore` 弹出登录框，`img.qrcode-img` 的 src 本身就是 base64 dataURL
  2. `browser_evaluate` 取出 src，解码写到项目根下用户可见处（如 `./xhs-login-qr-1.png`）交给用户手机扫；登录成功后删除
  3. QR 约 2-3 分钟过期；过期后刷新页面重抠（**换文件名**，避免查看端缓存旧图）
  4. 一次扫码登录态长期留 profile

## 关键机制：笔记 URL 必须带 xsec_token（最大的坑）

- 裸开 `https://www.xiaohongshu.com/explore/<note_id>` → 302 到 404 页（error_msg=当前笔记暂时无法浏览）。
- **token 只能从列表页 DOM 拿，不可跨会话缓存复用**：搜索结果卡片 `section.note-item` 内 `a.cover` / `a.title` 的 href 形如 `/search_result/<id>?xsec_token=...&xsec_source=`，直接 `browser_navigate` 该 href 即可（自动跳到 `/explore/<id>?xsec_token=...` 正常渲染）。
- ⚠️ `browser_evaluate` 里合成 `a.click()` 会绕过 SPA 事件处理、按裸 href 真导航 → 404。要么 navigate 完整 href（推荐），要么 `browser_click`（trusted click）。

## 工作流 ① 搜索

```
browser_navigate https://www.xiaohongshu.com/search_result?keyword=<urlencoded>
```

navigate 后先 `browser_wait_for` 等 `section.note-item` 出现（SPA 首屏渲染有延迟），再 evaluate。

`browser_evaluate` 批量提取（一页约 20 条）：

```js
() => JSON.stringify([...document.querySelectorAll('section.note-item')].map(s => ({
  title: s.querySelector('a.title')?.innerText?.trim(),
  href: (s.querySelector('a.cover') || s.querySelector('a.title'))?.href,
  likes: s.querySelector('.like-wrapper .count')?.innerText?.trim(),
  author: s.querySelector('.author .name, .author-wrapper .name')?.innerText?.trim()
})).filter(x => x.href))
```

## 工作流 ② 笔记详情 + 评论

navigate 带 token 的 href，然后一次提取：

| 字段 | 选择器 |
|------|--------|
| 标题 | `#detail-title` |
| 正文 | `#detail-desc` |
| 作者 | `.author-container .username` |
| 日期 | `.bottom-container .date` |
| 评论总数 | `.comments-container .total` |
| 评论项 | `.comment-item`（作者 `.author .name`、内容 `.content`、点赞 `.like .count`） |

长评论区滚动加载：每轮 `browser_evaluate` 执行 `() => { window.scrollTo(0, document.body.scrollHeight); const el = document.querySelector('.note-scroller'); if (el) el.scrollTop = el.scrollHeight; }`（页面与内层滚动容器是两个滚动面，需同时推进），轮间用 `browser_wait_for`（time ~0.7s）等待加载，**上限 8 轮**（约可加载百余条）。

经验：视频帖正文常只有 hashtag，信息在评论区；「求推荐」类帖的评论区是金矿（店名/避雷/票选都在评论里）。

## 工作流 ③ 批量下载落盘

1. **选篇**：高赞优先 + 作者多样性 + 标题视角互补（测评/红黑榜/对比贴混搭），默认 5-10 篇
2. 逐篇走工作流 ②
3. **落盘** `<xiaohongshu_raw_dir>/小红书-<主题>-<YYYY-MM-DD>/`：
   - 一篇一 md：`<序号两位>-<标题slug>.md`，frontmatter：

     ```yaml
     ---
     url: <带 xsec_token 的完整 URL>
     note_id: <id>
     author: <作者>
     date: <发布日期>
     likes: <赞数>
     fetched_at: <YYYY-MM-DD HH:MM>
     ---
     ```

     正文之后接 `## 评论`（作者/内容/赞，一行一条）
   - `index.md`：汇总表（标题/作者/赞/评论数/文件名）

## 可信度判别

本源属 UGC，按插件可信度阶梯**整体封顶 medium**；以下判别用于源内相对分级与剔除噪声：

- 评论全是「馋了/想吃」无价格无细节 = 疑似推广（**low，建议丢弃**）
- 多人指控「外地 IP 同话术」= 水军（low，丢弃）
- 独立多源交叉（≥2 个不相关账号同结论）= **medium（本源上限）**
- 单篇详实测评（带负面点、具体价格）= medium-low

## 坑表

| 症状 | 根因与处置 |
|------|-----------|
| 笔记页 404 | 裸 URL 无 token / token 失效 → 回搜索页重取，不要重试裸 URL |
| `Browser is already in use for <profile>` | 残留 Chrome 占 profile：`ps aux \| grep "user-data-dir=<profile>"`，kill 主进程（`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` 那条），登录态在磁盘不受影响，重试即可 |
| 搜索页空结果/被弹登录框 | 登录态失效 → 走登录引导 |
| 评论抓不全 | 滚动 8 轮上限，长评论区只保证百余条，结论里明示局限 |
