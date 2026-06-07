# Contract Sweep Report

- branch: `feat/wave2-contracts` @ `2291428`
- date: 2026-06-07 10:46
- jobs: 4 / total: 3610s
- **PASS 24 / FAIL 3 / SKIP 2 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| deep-research:deep-research#deep-research-kb | FAIL | env | 2 | 3600s | lingzhi/claude-opus-4-8 | timeout 1800s (kept: /var/folders/yx/h9t2knj942n72dsw5bq5b0z80000gn/T/katana-contracts.grebaadd/cases/deep-research-kb-retry) |
| fpa:fpa#fpa-full | FAIL | unknown | 2 | 1371s | lingzhi/claude-opus-4-8 | script: slug=contradiction-interlink-no-flatten  ✓
运行 validate_fpa.py FPA-contradiction-interlink-no-flatten.md ...
Trac (kept: /var/folders/yx/h9t2knj942n72dsw5bq5b0z80000gn/T/katana-contracts.grebaadd/cases/fpa-full-retry) |
| fpa:first-principles-thinking#fpt-lite | PASS | — | 1 | 36s | lingzhi/claude-opus-4-8 |  |
| guide:using-katana#using-katana-injected | PASS | — | 1 | 32s | lingzhi/claude-opus-4-8 |  |
| memory:remember#remember-card | PASS | — | 1 | 51s | lingzhi/claude-opus-4-8 |  |
| memory:validate#validate-cards | PASS | — | 1 | 71s | lingzhi/claude-opus-4-8 |  |
| obsidian-md:obsidian-writing#write-note | PASS | — | 1 | 34s | lingzhi/claude-opus-4-8 |  |
| retrieval:agent-session-search#agent-session-search | PASS | — | 1 | 86s | lingzhi/claude-opus-4-8 |  |
| retrieval:code#code-local-repo | PASS | — | 1 | 40s | lingzhi/claude-opus-4-8 |  |
| retrieval:feishu#feishu-doc-search | PASS | — | 1 | 56s | lingzhi/claude-opus-4-8 |  |
| retrieval:github#github-repo-lookup | PASS | — | 2 | 52s | lingzhi/claude-opus-4-8 |  |
| retrieval:gitlab#gitlab-project-lookup | PASS | — | 1 | 87s | lingzhi/claude-opus-4-8 |  |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | — | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | PASS | — | 1 | 35s | lingzhi/claude-opus-4-8 |  |
| retrieval:reddit#reddit-search | PASS | — | 1 | 74s | lingzhi/claude-opus-4-8 |  |
| retrieval:route#route-three-queries | PASS | — | 1 | 20s | lingzhi/claude-opus-4-8 |  |
| retrieval:search-note#search-note-local | PASS | — | 1 | 64s | lingzhi/claude-opus-4-8 |  |
| retrieval:twitter#twitter-fetch | PASS | — | 1 | 19s | lingzhi/claude-opus-4-8 |  |
| retrieval:using-retrieval#using-retrieval-loader | PASS | — | 1 | 19s | lingzhi/claude-opus-4-8 |  |
| retrieval:web#web-fetch | PASS | — | 2 | 66s | lingzhi/claude-opus-4-8 |  |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | — | dir missing: $KATANA_TEST_XHS_PROFILE |
| wiki:ingest#ingest-inbox | PASS | — | 1 | 117s | lingzhi/claude-opus-4-8 |  |
| wiki:init#init-adopt | FAIL | unknown | 2 | 200s | lingzhi/claude-opus-4-8 | file_grep: pattern 'Write Policy' not in WIKI.md (kept: /var/folders/yx/h9t2knj942n72dsw5bq5b0z80000gn/T/katana-contracts.grebaadd/cases/init-adopt-retry) |
| wiki:lint#lint-full | PASS | — | 1 | 263s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 47s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 40s | lingzhi/claude-opus-4-8 |  |
| wiki:using-wiki#using-wiki-ironrules | PASS | — | 1 | 49s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 54s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-save | PASS | — | 1 | 63s | lingzhi/claude-opus-4-8 |  |

## Skipped

- retrieval:linear#linear-issue-query: env LINEAR_API_KEY unset
- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

- none

## Overall Verdict

_(not run)_
