# Contract Sweep Report

- branch: `chore/remove-feishu-docs` @ `5cada11`
- date: 2026-11-09 09:30
- jobs: 4 / total: 373s
- **PASS 10 / FAIL 1 / SKIP 9 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| retrieval:code#code-local-repo | SKIP | — | 0 | 0s | glm-5.3 | dir missing: /Volumes/Data/code/self/katana |
| retrieval:feishu#feishu-doc-search | PASS | — | 1 | 227s | glm-5.3 |  |
| retrieval:github#github-repo-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:gitlab#gitlab-project-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | glm-5.3 | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:reddit#reddit-search | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:route#route-three-queries | PASS | — | 1 | 53s | glm-5.3 |  |
| retrieval:search-note#search-note-local | PASS | — | 1 | 68s | glm-5.3 |  |
| retrieval:twitter#twitter-fetch | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:web#web-fetch | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | glm-5.3 | dir missing: $KATANA_TEST_XHS_PROFILE |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 75s | glm-5.3 |  |
| work-folder:checkpoint#checkpoint-save | FAIL | unknown | 1 | 73s | glm-5.3 | process/tool_absent: tool 'Read' unexpectedly used (kept: /data/tmp/katana-sweep/katana-contracts.7mm1d0mu/cases/checkpo |
| writing:bluf#bluf-structure | PASS | — | 1 | 45s | glm-5.3 |  |
| writing:readability-check#distill-mode | PASS | — | 1 | 71s | glm-5.3 |  |
| writing:readability-check#evolve-triage | PASS | — | 1 | 258s | glm-5.3 |  |
| writing:readability-check#readability-check-workflow | PASS | — | 1 | 147s | glm-5.3 |  |
| writing:write#write-smoke | PASS | — | 1 | 133s | glm-5.3 |  |
| writing:write#write-template-instantiate | PASS | — | 1 | 142s | glm-5.3 |  |

## Skipped

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

- none

## Overall Verdict

_(not run)_
