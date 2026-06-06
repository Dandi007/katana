# WIKI — 测试知识库 Schema

## Zones
| zone | 路径 | 写策略 |
|---|---|---|
| 笔记 | 笔记/ | ingest 提案制 |
| raw | raw/ | 只进不改 |

## Page Conventions
中文命名；frontmatter 含 `source:`；正文末尾 `# References`。

## Write Policy
所有写入走 /wiki:ingest 提案；冲突必须命名互链，禁止抹平。

## Provenance
每页 frontmatter `source:` 指向 raw/ 或外部 URL。

## Indexing
索引页 `笔记/INDEX.md`，新页必须登记。

## Linting
矛盾点名、孤儿页、provenance 缺失。

## Log
`wiki-log.md` 逐条记录 ingest/lint 动作。

## Gaps
查询不覆盖时写 `gap-log.md`。
