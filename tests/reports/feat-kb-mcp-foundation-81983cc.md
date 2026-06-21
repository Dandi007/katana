# Contract Sweep Report

- branch: `feat/kb-mcp-foundation` @ `81983cc`
- date: 2026-06-22 00:23
- jobs: 4 / total: 665s
- **PASS 25 / FAIL 0 / SKIP 11 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| deep-research:deep-research#deep-research-kb | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| feishu-docs:feishu-docs#feishu-docs-config | PASS | — | 1 | 83s | lingzhi/claude-opus-4-8 |  |
| fpa:fpa#fpa-full | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| fpa:first-principles-thinking#fpt-lite | PASS | — | 1 | 46s | lingzhi/claude-opus-4-8 |  |
| incubate:incubate#incubate-e2e | PASS | — | 1 | 295s | lingzhi/claude-opus-4-8 |  |
| jury:review#review-smoke | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_JURY unset |
| memory:remember#remember-card | PASS | — | 1 | 53s | lingzhi/claude-opus-4-8 |  |
| memory:validate#validate-cards | PASS | — | 1 | 80s | lingzhi/claude-opus-4-8 |  |
| obsidian-md:obsidian-writing#write-note | PASS | — | 1 | 44s | lingzhi/claude-opus-4-8 |  |
| retrieval:agent-session-search#agent-session-search | PASS | — | 1 | 113s | lingzhi/claude-opus-4-8 |  |
| retrieval:code#code-local-repo | PASS | — | 1 | 57s | lingzhi/claude-opus-4-8 |  |
| retrieval:feishu#feishu-doc-search | PASS | — | 1 | 107s | lingzhi/claude-opus-4-8 |  |
| retrieval:github#github-repo-lookup | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:gitlab#gitlab-project-lookup | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:reddit#reddit-search | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:route#route-three-queries | PASS | — | 1 | 49s | lingzhi/claude-opus-4-8 |  |
| retrieval:search-note#search-note-local | PASS | — | 1 | 53s | lingzhi/claude-opus-4-8 |  |
| retrieval:twitter#twitter-fetch | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:web#web-fetch | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | dir missing: $KATANA_TEST_XHS_PROFILE |
| wiki:ingest#ingest-inbox | PASS | — | 1 | 101s | lingzhi/claude-opus-4-8 |  |
| wiki:init#init-adopt | PASS | — | 1 | 109s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-full | PASS | — | 1 | 318s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-summary-backfill | PASS | — | 1 | 87s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 98s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 58s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 92s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-save | PASS | — | 1 | 104s | lingzhi/claude-opus-4-8 |  |
| writing:bluf#bluf-structure | PASS | — | 1 | 25s | lingzhi/claude-opus-4-8 |  |
| writing:readability-check#distill-mode | PASS | — | 1 | 61s | lingzhi/claude-opus-4-8 |  |
| writing:readability-check#evolve-triage | PASS | — | 1 | 179s | lingzhi/claude-opus-4-8 |  |
| writing:readability-check#readability-check-workflow | PASS | — | 1 | 32s | lingzhi/claude-opus-4-8 |  |
| writing:write#write-smoke | PASS | — | 1 | 30s | lingzhi/claude-opus-4-8 |  |
| writing:write#write-template-instantiate | PASS | — | 1 | 49s | lingzhi/claude-opus-4-8 |  |

## Skipped

- deep-research:deep-research#deep-research-kb: env KATANA_E2E_NETWORK unset
- fpa:fpa#fpa-full: env KATANA_E2E_NETWORK unset
- jury:review#review-smoke: env KATANA_E2E_JURY unset
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
