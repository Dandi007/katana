---
name: using-retrieval
description: Hook-injected convention layer for the retrieval plugin. Governs how to ground factual answers in retrieved sources, route via /retrieval:route, and cite with credibility.
---

# Using Retrieval

本项目启用多源检索（{{SOURCES}}）。回答事实问题前先经 `/retrieval:route` 定源、勿凭参数化知识裸答；每条结论带 source + 可信度（high/medium/low，降级结果封顶 medium）。其它 skill 需抓外部源时也优先经 `/retrieval:<source>`（拿 fallback 链与可信度标注）。各源抓取细则、fallback 阶梯随 `/retrieval:*` 调用载入。
