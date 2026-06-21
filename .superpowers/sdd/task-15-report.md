# Task 15 Report：闸门升级 + 原子退役

**status:** DONE  
**commit:** 06f2a90  
**branch:** feat/e2e-harness-v2

---

## 删除的文件

| 文件 | 原因 |
|---|---|
| `tests/harness/case.py` | 功能已分别进 isolate.py / runner.py (CaseResult) |
| `tests/harness/asserts.py` | 功能已进 expect_process.py / expect_fs.py |
| `tests/harness/claude_cli.py` | 功能已进 trigger.py |
| `tests/unit/test_asserts.py` | 旧测试，已由 test_expect_process.py 取代 |
| `tests/unit/test_claude_cli.py` | 旧测试，已由 test_trigger.py 取代 |

## 修改的文件

| 文件 | 改动 |
|---|---|
| `tests/lint-structure.sh` | 加 3b(stdout_grep=0) + 3c(KB_DIR=0 in contract scripts) |
| `tests/unit/test_report.py` | 导入从 `harness.case.CaseResult` 改为 `runner.CaseResult` |
| `plugins/retrieval/tests/contracts/xiaohongshu-download.verify.sh` | `$KB_DIR` → `$CWD`（实际 bug 修复） |
| `plugins/incubate/tests/contracts/incubate-e2e.verify.sh` | 注释更新 KB_DIR→CWD |
| `plugins/memory/tests/contracts/remember-card.verify.sh` | 注释更新 KB_DIR→CWD |

---

## 5 项验证结果

| 验证 | 结果 |
|---|---|
| `pytest tests/unit -v` | **67 passed** in 5.06s |
| `bash tests/run-shell-tests.sh` | **9 passed** / 0 failed |
| `./tests/lint-structure.sh` | **G0 PASS**（40 valid + stdout_grep=0 + KB_DIR=0） |
| `runner.py --validate-only` | **40 contracts valid** |
| `bun test parity/adapter/opencode/` | **13 passed** |

## stdout_grep / KB_DIR grep 命中数

- `stdout_grep`（plugins+tests，排 lint-structure/test_schema/reports/md 文件）：**0 命中**
- `KB_DIR`（plugins/\*/tests/contracts/*.sh）：**0 命中**（xiaohongshu 已修为 `$CWD`）

---

## 偏离与注意事项

1. **KB_DIR 闸门范围收窄**：原始 plan 写 `grep -rn "KB_DIR" plugins/*/tests`，但 deep-research workflow.js 内部用了 `KB_DIR` 作 JS 局部变量名（非 harness env），retrieval/SKILL.md 里有文档示例，wiki/docs/plans/ 有旧归档计划。闸门改为只扫 `tests/contracts/*.sh`（harness script 逃逸口），更精确匹配意图。

2. **xiaohongshu-download.verify.sh 是真实 bug**：该脚本用 `$KB_DIR` 但 expect_fs.py 只注入 `CWD`/`DELTA_JSON`/`PATH`，实际跑时 `$KB_DIR` 为空 → `find` 返空 → 脚本 FAIL。已修为 `$CWD`。

3. **test_report.py 导入路径**：report.py 的 `CaseResult` 注释说"v2 三轴 CaseResult 格式"但实际类定义在 runner.py（非 report.py 自身）；test_report.py 原先从 `harness.case` 导入（旧位置），改为从 `runner` 导入，行为不变。
