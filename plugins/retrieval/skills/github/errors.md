## 2026-07-11 21:04 — public GitHub API 也要求 gh auth

- 错误信息：使用独立空 `GH_CONFIG_DIR` 执行 `gh api repos/containers/bubblewrap/releases/tags/v0.11.1` 时，CLI 要求先 `gh auth login` 或提供 `GH_TOKEN`。
- 现场：查询公开 release metadata，不需要私有身份；本机正式 gh config 权限受保护，不应为公开读取临时放宽或复制credential。
- 降级：按本skill的“API backup”直接调用GitHub public REST API；只读、无token，并校验HTTP失败。
## 2026-07-28 23:46 — `gh pr view` 不支持 `baseRefOid`

- 场景：核对 PR 的 mergeability 与 head/base identity。
- 现象：`gh pr view --json ... baseRefOid ...` 返回 `Unknown JSON field: "baseRefOid"`。
- 处理：从字段列表移除 `baseRefOid`，改用 `headRefOid`、`baseRefName`、`mergeable`、`mergeStateStatus` 与 `potentialMergeCommit`；若必须获得 base OID，另用 GitHub API/branch ref 查询。
