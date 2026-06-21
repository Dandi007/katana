# Task 7 Report — 轴② delta 断言（expect_fs.py）

## Status
DONE

## Commit
5ebdfd6  feat(harness2): 轴② delta 断言（created/modified/deleted/content/unchanged_outside/script）

## Pytest 汇总
```
2 passed in 0.01s
tests/unit/test_expect_fs.py::test_created_and_unchanged_outside PASSED
tests/unit/test_expect_fs.py::test_unchanged_outside_fails_on_stray_write PASSED
```

## 产出文件
- `tests/harness/expect_fs.py`：`check_fs(asserts, delta, cwd, contract_dir) -> list[Result]`
  - 支持类型：created / modified / deleted（fnmatch glob 匹配 delta 集合）/ content（regex grep delta 文件）/ unchanged_outside（累积 declared glob，断言无越界写）/ script（bash 逃逸口，传 CWD/DELTA_JSON env）
- `tests/unit/test_expect_fs.py`：2 个测试，与 plan Step1 逐字一致

## 关键实现说明
- `declared` 在遍历 asserts 时逐条累积 created/modified/deleted 的 glob；unchanged_outside 消费 declared，因此必须放断言列表最后，才能看到所有声明——代码注释已标注。
- `unchanged_outside` 值（`True`）在 plan 测试中为 Python `True`，`((typ, val),) = a.items()` 解包后 val 不参与逻辑，只 typ 决定分支，行为正确。

## 偏离 / Concern
无。代码与 plan Step3 完全对齐，测试与 plan Step1 完全对齐。
