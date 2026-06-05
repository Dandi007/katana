# Reddit Skill Errors

记录 Reddit skill 在使用、调试过程中发现的问题。

## 已知问题

暂无。

## 排查清单

- **HTTP 429**：触发速率限制，等待后重试。公开 API 限制 100 req/min
- **HTTP 403**：User-Agent 未设置或被 Reddit 拒绝，检查 `credential.py`
- **HTTP 404**：subreddit 或帖子不存在，或为私有 subreddit
- **连接超时**：检查网络代理设置，Reddit 在部分地区需要代理

## [2026-06-05] reddit.com 对本机全部出口 IP 级 403，公开 JSON API 不可用；arctic-shift 存档 API 为已验证降级路径

- **现象**：`get_post.py` / 裸 curl（www / old.reddit，含浏览器 UA 与规范 UA）经 mihomo(BWG) 与直连（本机 SG 出口）一律 403 "Blocked"（HTML 块页）；exa fetch 报 SOURCE_NOT_AVAILABLE；r.jina.ai 超时。判定为 Reddit 对数据中心/非住宅 IP 段的 IP 级封锁，与 User-Agent 无关。
- **降级路径（已验证）**：arctic-shift 存档 API，无需认证、走代理可达：
  - 帖子：`curl --proxy http://127.0.0.1:7897 "https://arctic-shift.photon-reddit.com/api/posts/ids?ids=<id>"`
  - 评论：`curl --proxy http://127.0.0.1:7897 "https://arctic-shift.photon-reddit.com/api/comments/search?link_id=<id>&limit=100"`
  - 字段与 Reddit 原生 JSON 基本一致（含 score/author/body/selftext/created_utc）；为存档快照，分数为抓取时点值。
- **pullpush.io 同类 API 当日返回空 data**，优先 arctic-shift。
- **启示**：skill 的"公开 JSON API 无需认证"前提对被封 IP 段不成立；遇 403 别浪费时间换 UA，直接走存档 API。
