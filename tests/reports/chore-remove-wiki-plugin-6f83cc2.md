# Contract Sweep Report

- branch: `chore/remove-wiki-plugin` @ `6f83cc2`
- date: 2026-09-19 16:42
- jobs: 4 / total: 9s
- **PASS 0 / FAIL 12 / SKIP 9 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| feishu-docs:feishu-docs#feishu-docs-config | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | fs/created: no created match: feishu-docs-answer.md; fs/content: content path not in delta: feishu-docs-answer.md; fs/co |
| retrieval:code#code-local-repo | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | dir missing: /Volumes/Data/code/self/katana |
| retrieval:feishu#feishu-doc-search | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'retrieval:feishu' not loaded; fs/created: no created match: feishu-doc-search-result.md; fs |
| retrieval:github#github-repo-lookup | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:gitlab#gitlab-project-lookup | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:reddit#reddit-search | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:route#route-three-queries | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'retrieval:route' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/route-three-queries |
| retrieval:search-note#search-note-local | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'retrieval:search-note' not loaded; fs/created: no created match: search-result.md (kept: /t |
| retrieval:twitter#twitter-fetch | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:web#web-fetch | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | env KATANA_E2E_NETWORK unset |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | lingzhi/claude-opus-4-8 | dir missing: $KATANA_TEST_XHS_PROFILE |
| work-folder:checkpoint#checkpoint-resume | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'work-folder:checkpoint' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/checkpoint-r |
| work-folder:checkpoint#checkpoint-save | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'work-folder:checkpoint' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/checkpoint-s |
| writing:bluf#bluf-structure | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'writing:bluf' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/bluf-structure) |
| writing:readability-check#distill-mode | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'writing:readability-check' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/distill-m |
| writing:readability-check#evolve-triage | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | fs/created: no created match: evolve-triage-answer.md; fs/content: content path not in delta: evolve-triage-answer.md; f |
| writing:readability-check#readability-check-workflow | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'writing:readability-check' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/readabili |
| writing:write#write-smoke | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'writing:write' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/write-smoke) |
| writing:write#write-template-instantiate | FAIL | unknown | 1 | 2s | lingzhi/claude-opus-4-8 | process/skill_loaded: skill 'writing:write' not loaded (kept: /tmp/katana-contracts._vfv_vsb/cases/write-template-instan |

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
