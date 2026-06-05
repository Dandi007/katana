---
name: reddit
description: Reddit 检索源。公开 JSON API 取帖/评论/subreddit；reddit.com 对 datacenter IP 全 403 时降级 arctic-shift 存档 API。
---

# /retrieval:reddit

主路：`scripts/get_post.py` / `get_posts.py` / `search_posts.py`（公开 JSON API，须设 User-Agent + 代理）。
**降级（已验证）**：reddit.com 对本机出口 IP 段 403 → `RETRIEVAL_ARCHIVE_API=<reddit_archive_api> python3 scripts/archive_get.py post <id>` / `comments <link_id> --limit 100`。
踩坑详见 errors.md：换 UA 无用、直接走存档 API；pullpush 当日可能空，优先 arctic-shift。

配置：`reddit_archive_api`、`web_proxy`（.katana）。降级结果可信度封顶 medium。
