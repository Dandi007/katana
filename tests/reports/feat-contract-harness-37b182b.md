# Contract Sweep Report

- branch: `feat/contract-harness` @ `37b182b`
- date: 2026-06-07 01:25
- jobs: 1 / total: 615s
- **PASS 5 / FAIL 1 / SKIP 1 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | — | dir missing: $KATANA_TEST_XHS_PROFILE |
| wiki:ingest#ingest-inbox | PASS | — | 1 | 87s | lingzhi/claude-opus-4-8 |  |
| wiki:lint#lint-full | PASS | — | 1 | 198s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-cold | PASS | — | 1 | 58s | lingzhi/claude-opus-4-8 |  |
| wiki:query#query-hot | PASS | — | 1 | 43s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 81s | lingzhi/claude-opus-4-8 |  |
| work-folder:checkpoint#checkpoint-save | FAIL | unknown | 2 | 144s | lingzhi/claude-opus-4-8 | file_grep: pattern 'Updated: 2026' not in progress.md (kept: /var/folders/yx/h9t2knj942n72dsw5bq5b0z80000gn/T/katana-contracts.dinnlq6x/cases/checkpoint-save-retry) |

## Skipped

- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

- none

## Overall Verdict

_(not run)_
