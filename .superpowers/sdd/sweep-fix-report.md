# Sweep Fix Report — feat/e2e-harness-v2

**Commit:** 710c3d6
**Branch:** feat/e2e-harness-v2
**Date:** 2026-06-21

---

## Status: COMPLETE

All 10 originally-failing contract cases resolved. Commit 710c3d6.

---

## Group B — 补合法写声明（5 cases）

实跑结果（全部 PASS，`--skip-judge`，model=lingzhi/claude-opus-4-8）：

| case | result | 耗时 |
|---|---|---|
| incubate:incubate#incubate-e2e | PASS | 306s |
| wiki:init#init-adopt | PASS | 99s |
| wiki:lint#lint-full | PASS | 359s |
| wiki:lint#lint-summary-backfill | PASS | 98s |
| work-folder:checkpoint#checkpoint-resume | PASS | 106s |

### 关键发现

**新增 `allowed` 断言类型**（`tests/harness/schema.py` + `tests/harness/expect_fs.py`）：
- 原 `created` 类型要求文件必须存在，不适合非确定性可选输出（如 `.wiki/lint/*.md`）。
- `allowed` 只把 glob 加入 `unchanged_outside` 白名单，不产生 FAIL 断言。
- `wiki-log.md`（lint-full）：fixture `kb` 中已预存，但 agent 有时会修改它 → 用 `allowed`（非 `created`）。
- `log.md`（lint-summary-backfill）：agent 可选写入，非必写 → 用 `allowed`。

各契约具体变更：
- **incubate-e2e**: 声明 `Incubator/*/*/*/{context,findings,progress,golden-order}.md`（4 个 created）
- **init-adopt**: 声明 `log.md`/`index.md`/`inbox/.gitkeep`/`.katana`（wiki init 确定性脚手架，created）
- **lint-full**: `allowed: .wiki/lint/*.md` + `allowed: wiki-log.md`
- **lint-summary-backfill**: `allowed: .wiki/lint/*.md` + `allowed: wiki-log.md` + `allowed: log.md`
- **checkpoint-resume**: `created: 工作记录/fixture-task/context.md` + `created: 工作记录/fixture-task/findings.md`

---

## Group C — search-note 内容匹配转 semantic（1 case）

实跑结果：

| case | result | 耗时 |
|---|---|---|
| retrieval:search-note#search-note-local | PASS | 62s |

变更：
- 移除 `content: {matches: "意式浓缩温度-A"}` 和 `content: {matches: "意式浓缩温度-B"}` 硬断言（模型方差：pilot PASS、sweep FAIL）。
- 新增 `tests/judge/case-rubrics/search-note.md`（3 题：A/B 各命中 + 同时包含两派）。
- 保留确定性锚：`skill_loaded: retrieval:search-note` + `created: search-result.md`。
- `--skip-judge` 下 process + filesystem 绿，semantic 留给 judge 运行。

---

## Group A — using-* 注入约定改机械 hook 测试 + 豁免（4 skills）

| inject test | result |
|---|---|
| plugins/guide/tests/session-start-inject.test.sh | PASS (3/3) |
| plugins/retrieval/tests/session-start-inject.test.sh | PASS (4/4) |
| plugins/wiki/tests/session-start-inject.test.sh | PASS (4/4) |
| plugins/writing/tests/session-start-inject.test.sh | PASS (4/4) |

变更：
- `git rm` 4 个 `using-*.contract.yaml`（`skill_loaded` 永不出现在 SessionStart 注入的 agent trace 中，是迁移错误）。
- 新增 4 个 `session-start-inject.test.sh`，直接执行 hook 脚本，断言 hookSpecificOutput + 关键约定词 + 占位符替换。
- `tests/coverage-exemptions.txt` 加 4 条豁免，注明指向对应机械测试。

---

## 验证结果

| 检查项 | 结果 |
|---|---|
| `bash tests/run-shell-tests.sh` | 13/13 PASS（含 4 个新 inject 测试） |
| `bash tests/lint-structure.sh` | G0 PASS（36 contracts valid） |
| `pytest tests/unit` | 67/67 passed |

---

## 偏差与说明

1. **新增 `allowed` 断言类型**：任务描述未预见此需求，但 `created` 不能表达"可选输出"语义。`allowed` 最小侵入（schema + expect_fs 各 3 行），向后兼容，单元测试全过。

2. **lint-full 额外迭代**：第一次修复用 `created: wiki-log.md` 导致 FAIL（`wiki-log.md` 在 `kb` fixture 中预存，被 agent 修改而非创建，故 `delta["created"]` 找不到它）。第二次改为 `allowed` 修复。

3. **lint-summary-backfill `log.md` vs `wiki-log.md`**：sweep artifact 显示写的是 `wiki-log.md`，但实际运行写的是 `log.md`（模型方差）。两个都加 `allowed` 兜底。
