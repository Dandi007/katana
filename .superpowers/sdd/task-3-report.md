# Task 3 Report: filesystem 快照 + delta

## status
DONE

## commit
723cfc5

## pytest 汇总
```
2 passed in 0.02s
tests/unit/test_snapshot.py::test_delta_created_modified_deleted PASSED
tests/unit/test_snapshot.py::test_excludes_harness_paths PASSED
```

## 交付物
- `tests/harness/snapshot.py`：`snapshot(root, exclude)`→dict[relpath,sha256]；`delta(before,after)`→{created,modified,deleted}；常量 `HARNESS_EXCLUDE`；`_excluded` 按首段或全名匹配。
- `tests/unit/test_snapshot.py`：2 个测试，逐字 plan Step1。

## 偏离 / concern
无。实现与 plan Step3 代码逐字一致，测试与 plan Step1 逐字一致，验收命令与预期完全符合。
