## [2026-07-16 09:40] fs_create 全部失败：_quarantine 内损坏 frontmatter 绊死 mutation 管线

- **现象**：`fs_create` 返回 `OPERATION_FAILED`，YAML 报错内容（`clue_id: c4_6`…）与提交的 payload 完全无关。
- **根因**：katana-wiki-mcp 在 mutation 时全库扫 frontmatter，**未排除 `_quarantine/`**；隔离区里 11 个文件本就因 frontmatter 损坏被隔离（`tags: [#x]` 的 `#` 注释、双引号标量内未转义 `"`、未引号标量含 `: `），第一个被解析到的就让整个 mutation 崩掉。
- **修复**：对 11 个文件做保内容的引号/转义修复（/data/wiki commit 671d759），全库 frontmatter 可解析后 mutation 恢复。
- **待修 server bug**：mutation 管线的 frontmatter 扫描应排除 `_quarantine/`（或对单文件解析失败降级为 warning），否则任何新进隔离区的坏文件会再次全局锁死写入。
- **另**：`fs_write` 不隐式创建新文件（`RESOURCE_NOT_FOUND: write does not implicitly create`），新页面必须用 `fs_create`；`摘要` frontmatter 有 server 侧 >40 字硬校验。
