---
name: using-retrieval
description: Hook-injected convention layer for the retrieval plugin. Governs how to ground factual answers in retrieved sources, route via /retrieval:route, and cite with credibility.
---

# Using Retrieval

This project has multi-source retrieval enabled.

- enabled sources: {{SOURCES}}

## Iron Rules

1. **Route before answering.** 回答事实问题前先经 `/retrieval:route` 定源，勿凭参数化知识裸答。
2. **Cite with credibility.** 每条检索结论带 source + 可信度（high/medium/low）；降级结果封顶 medium。
3. **Ground-truth code.** 代码问题以源码为准，走 `/retrieval:code`（本地 code root，缺则自动 clone）。
4. **Respect the fallback ladder.** 主路失败按 fallback 协议降级，不静默放弃。
5. **Applies inside other skills too.** 当其它 skill（wiki:ingest 抓源、deep-research worker、explain/verify 取证）需要抓取**外部源**（推文/reddit/网页/repo/飞书等）时，**优先经 `/retrieval:<source>` 抓**，而不是各自 ad-hoc curl/WebFetch——这样才拿到 fallback 链（如 reddit 403→arctic-shift）与可信度标注。直接抓仅当 retrieval 插件未装或该源无对应 adapter。
