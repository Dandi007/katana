---
name: incubate
description: 想法孵化台。围绕一件具体的事情持续沉淀想法、对资料的评论和灵感，整理成日后 propose/设计文档的输入。当用户说「开个 X 的孵化」「把相关资料找来提炼」「记一下我对这个的想法」「把这些揉成材料」「拿去 propose/毕业」时使用。不用于一次性高保真 idea 卡（走 idea），也不用于交付驱动的正式 work folder。
---

# Incubate — 想法孵化台

围绕**一件具体的事情**，跑三步循环：①资料探索+提炼 → ②人类想法输入（实时） → ③整理成材料，
产出**喂给下游 propose/设计文档的输入**，与正式交付解耦。

复用 work folder 的控制面文件，不另造命名：

| 文件 | 承载 |
|------|------|
| `golden-order.md` | ②人类想法：原文优先、append-only、带时间戳 |
| `findings.md` | ①资料探索+提炼：每条 = 来源链接 + 核心点 |
| `spec.md` | ③成品材料：output-ready，毕业时拿去 propose |
| `context.md` | 动态关联、`[[wikilink]]`、资源快照 |
| `progress.md` | 状态（孵化中/已毕业）、下一步 |
| `README.md` | 入口：事情是什么 · 维度 · 状态 |

## 路径约定

```
Incubator/YYYY/MM/<topic-slug>/
```

- 顶层 `Incubator/`（与 `Ideas/`、`DeepThought/` 同级），月粒度日期层。
- 维度 `work` / `learning` 写进 README frontmatter，一套结构两维度共用。
- 执行任何写时间的动作前先 `date "+%Y-%m-%d %H:%M"`。

## 五个 mode（按意图分发，轻量、不强制走全套）

### init — 「开个 X 的孵化」/ 给定主题
1. `date` 取时间。
2. 建 `Incubator/YYYY/MM/<topic-slug>/`（slug 用描述性短语，按文件名安全规则清洗：移除 `/:\\?*"<>|`）。
3. 用 templates/ 初始化 `README.md`（填 topic/维度/状态=孵化中/创建日期）、`golden-order.md`、`findings.md`、`context.md`、`progress.md`、`spec.md`。
4. 向用户汇报孵化台路径。

### gather — 资料探索 / 「找找相关的」
1. 复用检索 skill 拉相关资料：本地走 `search-note` / `explore-work-record`；外部源**必须走 `/retrieval:*`**（拿 fallback 链 + 可信度标注），不 ad-hoc curl/WebFetch。
2. 每条资料 append 进 `findings.md`：`来源链接 + 1-3 句核心点提炼 + 可信度`。
3. 关联到的工作文档/笔记记进 `context.md`（路径 + `[[wikilink]]`）。

### capture — 语音口述想法
1. **实时**落盘，绝不攒到最后（golden-order 铁律：宁多勿漏、保留原话）。
2. append 进 `golden-order.md`，格式 `## [YYYY-MM-DD HH:MM] <主题>` + 用户原话（必要时附轻整理）。
3. 原文优先；轻整理不改写核心语义。

### synthesize — 「整理一下 / 揉成材料」
1. 读 `findings.md` + `golden-order.md`。
2. 揉成 output-ready 的 `spec.md`：成品要顺，是日后 propose 的直接输入。
3. 标注哪些来自资料、哪些来自人类想法。

### graduate — 「毕业 / 拿去 propose」
1. 把 `spec.md` 交给 `write-spec` 或新建正式 work folder（解耦：孵化台不变成交付目录）。
2. `README.md` 状态置「已毕业」，写明下游产出指向（spec/PR/work folder 路径）。
3. `progress.md` 记毕业时间。

## 禁止事项
- 不把人类原话压成过度抽象摘要。
- 不在 capture 阶段攒着批量整理。
- 外部源不绕过 `/retrieval:*` 直接抓。
- 不把孵化台当交付目录管理（goal/plan/execute 归正式 work folder）。

# References
- 设计文档：`智元工作/工作记录/2026/06/10/incubate-katana-plugin/design.md`
- 对照物 `idea` skill（一次性高保真卡）、work-folder 约定（控制面文件语义）
