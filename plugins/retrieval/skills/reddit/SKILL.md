---
name: reddit
description: Reddit 检索源。arctic-shift 存档 API 为主路（reddit.com 对 datacenter/proxy IP 全 403）；公开 JSON API 备用（仅限住宅 IP）。
---

# /retrieval:reddit

**主路（已验证）**：arctic-shift 存档 API，datacenter/proxy 出口可用。

```bash
RETRIEVAL_ARCHIVE_API="$(katana_config_get reddit_archive_api "https://arctic-shift.photon-reddit.com" "")"
RETRIEVAL_ARCHIVE_API="$RETRIEVAL_ARCHIVE_API" python3 scripts/archive_get.py post <id>
RETRIEVAL_ARCHIVE_API="$RETRIEVAL_ARCHIVE_API" python3 scripts/archive_get.py comments <link_id> --limit 100
```

踩坑详见 errors.md：reddit.com 公开 JSON API 对 datacenter/proxy IP 段 403，换 UA 无用；pullpush 当日可能为空，优先 arctic-shift。

**备用（住宅 IP 环境）**：`scripts/get_post.py` / `get_posts.py` / `search_posts.py`（公开 JSON API，须设 User-Agent + 代理）。

配置：`reddit_archive_api`、`web_proxy`（.katana）。存档 API 结果可信度封顶 medium。
