---
clue_id: <c0>
round: <1>
clue: "<线索描述>"
status: completed | partial | blocked
signals:                       # L1 证据信号，喂给 triage（事实层，非分数）
  hit_original_keyword: <true|false>
  high_density_source: <true|false>   # 官方文档/MR/snapshot 等高密度源
  time_author_aligned: <true|false>
timestamp: <ISO8601>
---

# Finding: <线索描述>

## L1 · Findings（摘要层，结构化）

| # | Title | Anchor | Type | Summary | Credibility |
|---|-------|--------|------|---------|-------------|
| 1 | <标题> | <URL/路径/file:line> | article/paper/doc/news/blog/forum/code/local/platform:<源名> | <一句摘要> | high/medium/low/conflicted |

## L2 · 原文摘录（按需深读层）

> 规则：只摘**与线索相关**的段落，**逐字**保留，**每段必带锚点**；不相关的不塞、不整页 dump。

### [1] <标题> — <Anchor>
```
<相关段落逐字原文（可多段，够长即可）>
```
- **Why relevant**: <为什么与线索相关，一句>

## New Clues（新线索）

| clue | why | suggested_sources | depth |
|------|-----|-------------------|-------|
| <新线索> | <为什么值得追> | web, local_text, <命名源名> | <当前 round> |

## Blocked

| source | reason |
|--------|--------|
| <source> | <0 命中 / 无权限 / 失效> |
