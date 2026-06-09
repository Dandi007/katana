# wiki 摘要索引层：每页 frontmatter `摘要` + lint backfill

**Date:** 2026-06-09
**Status:** design
**Scope:** Phase 1（本 spec）。Phase 2 `/wiki:explore`（关联走读 skill）是本层的头号消费者，单独立 spec，不在此实现。

## 要什么

wiki 每一页的 frontmatter 都带一行 `摘要`——一句话说清"这页讲什么"。这样任何检索方（query 的 orient、未来 explore 的 frontier、lint 的体检）想知道一页讲啥，**只读 frontmatter 一行即可，不必翻开正文全文**。

这正是 memory 与 skill 已经在用的模式：每个 memory card 有 frontmatter `description`、每个 SKILL.md 有 `description`，聚合成一个轻量索引层供路由/检索廉价命中。wiki 现在缺这一层，补上。

## 背景：现状与痛点

- 本库 592 篇原子卡片 + Index/MOC，frontmatter 只有 `创建日期 + tags`（部分页另有 `类型 / source_type / credibility / sources`）。**零页带摘要类字段。**
- 原子卡正文第一段才是 `## 概念定义`。所以"这页讲什么"这条信息**只存在于正文里**，任何检索方要拿到它都得读进正文。
- 对 query 的 orient（产候选列表，每条要写一句"为什么相关"）这是浪费；对 Phase 2 的 explore 更致命——它展开一圈 frontier 时要为每个邻居说一句"这是什么/为什么相连"，没有 frontmatter 摘要就得逐个翻全页，frontier 成本爆炸。
- memory/skill 把这条信息固化在 frontmatter `description` 里，检索方一行命中。wiki 照搬这个层即可。

## 决策

**加一个 schema 层字段，由现有三件消费/维护它，零新 skill。**

1. **schema 新增必填字段 `摘要`**（WIKI.md §3 / §5 / §7）。一行一句话，描述页面自身。
2. **ingest 生成它**：每次 create/update 页面都产出/刷新 `摘要`，纳入提案。源头从此不欠债。
3. **lint 检测并修复它**：扫受治理 zone，缺 `摘要` 的页报为 finding 并补上。首次全库跑 = 592 页一次性 backfill（Workflow fan-out 并行）；之后常态捡漏。
4. **范围限定**：只动受 wiki 治理的写入 zone（`Zettelkasten/` 顶层卡片 + `Zettelkasten/Index/`）。raw / inbox / 非治理目录一律不碰。

### 为什么不开新 skill

`摘要` 不是一类新知识，是页面自身内容的压缩（derived-from-self）。它的生命周期天然附着在"写页"（ingest）和"体检"（lint）上，没有独立工作流。新开 skill 是过度设计。

### 为什么 lint 可以写

lint 现契约是"只报不写，建页/合并是 ingest 的事"。本 spec **仅对 `摘要` 这一种 derived-from-self 元数据**放开这条：

- 它不是从外部源引入的新知识，model-collapse 防线（iron-rule-4）不适用——它只是把本页已有内容压一行，不存在"把未审合成输出当 source 再 ingest"的风险。
- 它不碰正文，只增删 frontmatter 一行，可逆、错了重生成即可。

ingest 仍是唯一的"知识写入"路径；lint 的写仅限这一个元数据字段。

## 实现

### 1. schema（WIKI.md）

§3 Page Conventions 新增：

- **`摘要`（new requirement，仅经 ingest/lint 维护的页适用）**：YAML 标量，一行一句话，≤ ~40 字。内容 = 这页讲什么 + 核心定义/结论。**只描述页面自身，不含与其他页的关系**（关系是 explore 运行时的产物，不进静态 frontmatter）。对原子卡 ≈ 其"一句话定义"的浓缩；对 Index/MOC ≈ 这篇聚合了什么。
- 放 frontmatter 顶部区（紧随 `创建日期` 之后，约定即可，不强制顺序）。
- 存量不要求人工回填，由 lint backfill 统一补。

§5 Ingest Specifics 增一句：ingest 新建/更新页必生成 `摘要`。
§7 Lint Rules 增一项：缺 `摘要` 为可修复 finding（见下）；raw / inbox 豁免。

### 2. ingest（plugins/wiki/skills/ingest/SKILL.md）

- 写页前生成 `摘要` 行，纳入提案包，跟随既有 propose gate 一起人工确认。
- update 页时：若本次内容变动使旧摘要过时，刷新摘要；否则保留。
- 摘要生成约束写进 skill：一句话、≤~40 字、描述页面自身、中文为主术语保留英文。

### 3. lint（plugins/wiki/skills/lint/SKILL.md）

- **机械检查**：遍历受治理 zone 的页，frontmatter 无 `摘要`（或为空）→ 计入 "missing-摘要" findings，报数量 + 页清单。
- **修复模式**：对缺摘要的页，读全页 → 生成一行摘要 → 插入 frontmatter（**不碰正文**）。
- **首次全库 bootstrap**：592 页用 Workflow fan-out 并行 summarizer，每 agent 读一页产一行。
- **写入治理（autonomous batch + 抽检）**：先生成随机 N 页样本（建议 N=10）展示给人工 QC 质量；通过后 fan-out 自动批量写全部缺摘要页，**不逐页 gate**。依据：derived 元数据、可逆、错了重跑覆盖。
- raw zones（`转换文档/`、`DeepThought/*/`）与 inbox 豁免，不在治理范围。

### 4. 测试 / 契约

- 新增 contract（参照 `plugins/wiki/tests/contracts/lint-full.contract.yaml`）：一个含/缺 `摘要` 的小 fixture 库，断言 lint 能报出缺摘要页数、修复后复检归零、且正文 byte 不变。
- ingest 契约补一条：新建页产物 frontmatter 含合理 `摘要`。

## 范围与非目标

- **只 Phase 1**：摘要字段 + ingest 生成 + lint 检测/修复/bootstrap。
- **非目标 / 刻意不做（YAGNI）**：
  - 不建常驻全局索引文件（MEMORY.md 那样）。592+ 页全量常驻吃不消，且要维护同步。检索方按需读邻居 frontmatter 已足够廉价。
  - 不启用 embedding（WIKI.md §8 仍 off）。
  - 不动非 wiki 治理目录（memory / 智元工作 / docs / Templates / .agents / .claude）。
  - `/wiki:explore`（关联走读）是独立的 Phase 2 spec，本层是其前提。

## 验收标准

1. WIKI.md §3 含 `摘要` 字段定义，§5 / §7 相应更新。
2. ingest 新建一页，产物 frontmatter 带合理 `摘要`。
3. lint 能在 fixture 库上列出缺摘要页数；运行修复后复检为 0；被修复页正文 byte 不变、仅 frontmatter 增一行。
4. 抽样存量库若干页，摘要一句话、贴合页面主旨、中文为主术语保留英文。
5. 新增/更新的 contract 全绿。

# References

- 现状勘验：`Zettelkasten/Zettelkasten/*.md`（592 页，frontmatter 仅 `创建日期 + tags`）
- 参照模式：memory plugin `description` + `MEMORY.md` 索引；skill `description` 路由索引
- schema SSoT：仓库根 `WIKI.md`
- lint 现契约："reports findings and proposes fixes, but never builds pages or merges them"（plugins/wiki/skills/lint/SKILL.md）
