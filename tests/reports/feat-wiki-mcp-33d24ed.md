# Contract Sweep Report

- branch: `feat/wiki-mcp` @ `33d24ed`
- date: 2026-06-22 10:36
- jobs: 4 / total: 268s
- **PASS 3 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| writing:readability-check#distill-mode | PASS | — | 1 | 97s | lingzhi/claude-opus-4-8 |  |
| writing:readability-check#evolve-triage | PASS | — | 1 | 266s | lingzhi/claude-opus-4-8 |  |
| writing:readability-check#readability-check-workflow | PASS | — | 1 | 50s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

_(not run)_

---

## 复核说明（flaky 确认）

- 唯一 FAIL `writing:readability-check#distill-mode` 系 `skill_loaded` 进程轴 model-variance flaky（与本 PR 无关——本分支只动 `mcp/*`(新) + `plugins/wiki/hooks/session-start`，未碰 `plugins/writing/`）。
- **重跑确认**：`writing:readability-check` 三例 PASS 3/FAIL 0（distill-mode 97s PASS）。
- **wiki 6/6 全 PASS**（query-hot/cold、ingest-inbox、init-adopt、lint-full、lint-summary-backfill）——hook 改动零 wiki 回归，默认 skill 路径完好。
