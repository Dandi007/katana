# Contract Sweep Report

- branch: `chore/retrieval-wiki-tool-names` @ `caf7c80`
- date: 2026-09-19 18:21
- jobs: 4 / total: 160s
- **PASS 2 / FAIL 0 / SKIP 10 / NEEDS-REVIEW 1**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| deep-research:deep-research#deep-research-kb | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:code#code-local-repo | SKIP | — | 0 | 0s | glm-5.3 | dir missing: /Volumes/Data/code/self/katana |
| retrieval:feishu#feishu-doc-search | PASS | — | 1 | 158s | glm-5.3 |  |
| retrieval:github#github-repo-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:gitlab#gitlab-project-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | glm-5.3 | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:reddit#reddit-search | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:route#route-three-queries | NEEDS-REVIEW | — | 1 | 83s | glm-5.3 | semantic judge non-PASS |
| retrieval:search-note#search-note-local | PASS | — | 1 | 92s | glm-5.3 |  |
| retrieval:twitter#twitter-fetch | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:web#web-fetch | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | glm-5.3 | dir missing: $KATANA_TEST_XHS_PROFILE |

## Skipped

- deep-research:deep-research#deep-research-kb: env KATANA_E2E_NETWORK unset
- retrieval:code#code-local-repo: dir missing: /Volumes/Data/code/self/katana
- retrieval:github#github-repo-lookup: env KATANA_E2E_NETWORK unset
- retrieval:gitlab#gitlab-project-lookup: env KATANA_E2E_NETWORK unset
- retrieval:linear#linear-issue-query: env LINEAR_API_KEY unset
- retrieval:official-docs#official-docs-lookup: env KATANA_E2E_NETWORK unset
- retrieval:reddit#reddit-search: env KATANA_E2E_NETWORK unset
- retrieval:twitter#twitter-fetch: env KATANA_E2E_NETWORK unset
- retrieval:web#web-fetch: env KATANA_E2E_NETWORK unset
- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

### retrieval:route#route-three-queries
- [yes] Query 1 (RX 7900 XTX 显卡 reddit 评价) 是否被明确路由到 reddit / retrieval:reddit? — 路由表第 1 行：源名称 **reddit**，入口 `/retrieval:reddit`，清晰命名而非隐含。
- [yes] Query 2 (React useEffect 官方文档) 是否被明确路由到 official-docs / retrieval:official-docs? — 路由表第 2 行：源名称 **official-docs**，入口 `/retrieval:official-docs`。
- [yes] Query 3 (本地笔记里关于意式浓缩) 是否被明确路由到 search-note / retrieval:search-note? — 路由表第 3 行：入口 `/retrieval:search-note`（源名称标注 local_text，但入口明确指向 search-note skill，满足判据中 `search-note` 或 `retrieval:search-note` 的要求）。
- [no] 补充说明中的降级备注（reddit/official-docs 未启用、降级走 web）是否影响路由判定? — 任务要求是路由而不实际执行检索；主路由结果均正确命名，降级备注只是执行层面的说明，不构成路由错误。

## Overall Verdict

_(not run)_
