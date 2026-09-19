# Contract Sweep Report

- branch: `chore/retrieval-wiki-tool-names` @ `f3f8bb8`
- date: 2026-09-19 18:17
- jobs: 4 / total: 192s
- **PASS 0 / FAIL 2 / SKIP 10 / NEEDS-REVIEW 1**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| deep-research:deep-research#deep-research-kb | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:code#code-local-repo | SKIP | — | 0 | 0s | glm-5.3 | dir missing: /Volumes/Data/code/self/katana |
| retrieval:feishu#feishu-doc-search | FAIL | unknown | 1 | 190s | glm-5.3 | fs/created: no created match: feishu-doc-search-result.md; fs/content: content path not in delta: feishu-doc-search-resu |
| retrieval:github#github-repo-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:gitlab#gitlab-project-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | glm-5.3 | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:reddit#reddit-search | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:route#route-three-queries | NEEDS-REVIEW | — | 1 | 68s | glm-5.3 | semantic judge non-PASS |
| retrieval:search-note#search-note-local | FAIL | unknown | 1 | 75s | glm-5.3 | fs/created: no created match: search-result.md (kept: /tmp/katana-contracts.qjcd4bz3/cases/search-note-local) |
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
- [no] Query 1 (RX 7900 XTX reddit 评价) → reddit 或 retrieval:reddit — 结果表第 1 行明确写「路由到的源: **web**」，说明栏为「意图命中 reddit，但该源未启用 → 按 fallback 降级到 web」——最终命名路由是 web，不是 reddit/retrieval:reddit（即便意图被识别，rubric 要求的路由目标未被采用）
- [no] Query 2 (React useEffect 官方文档) → official-docs 或 retrieval:official-docs — 结果表第 2 行明确写「路由到的源: **web**」，说明栏为「意图命中 official-docs，未启用 → 降级到 web」——最终命名路由是 web，不是 official-docs/retrieval:official-docs
- [yes] Query 3 (本地笔记里关于意式浓缩) → search-note 或 retrieval:search-note — 结果表第 3 行明确写「**search-note**（local_text）」且「入口 `/retrieval:search-note`」，源被清晰命名且正确

## Overall Verdict

_(not run)_
