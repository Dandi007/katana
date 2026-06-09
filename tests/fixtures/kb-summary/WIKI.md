# WIKI Schema (summary-backfill fixture)

## 2. Zones

| Zone | Path | Purpose | Write policy | Page template | Naming |
|------|------|---------|--------------|---------------|--------|
| 笔记 | `笔记/` | thinking | propose | 原子卡片 | 中文概念名，允许空格 |

## 3. Page Conventions

- **必填 frontmatter：**
  - `摘要`：一行一句话（≤~40 字），描述本页讲什么 + 核心定义/结论，只描述页面自身。new requirement，存量由 lint backfill。

## 7. Lint Rules

- **摘要 backfill：** 缺 `摘要` 的页为可修复 finding；lint 读全页生成一行并写入 frontmatter（不碰正文）。批量写走抽检 + 自动批量（见 lint skill §4）。raw / inbox 豁免。
