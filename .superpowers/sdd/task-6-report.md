# Task 6 Report — 轴① 过程断言（expect_process.py）

## status
DONE — 全部验收通过

## commit
ee8dd35  feat(harness2): 轴① 过程断言（skill_loaded/tool_used/absent/count/sequence）

## pytest 汇总
```
6 passed in 0.02s
tests/unit/test_expect_process.py::test_all_process_asserts_pass         PASSED
tests/unit/test_expect_process.py::test_skill_absent_returns_false        PASSED
tests/unit/test_expect_process.py::test_tool_used_absent_and_count_failures PASSED
tests/unit/test_expect_process.py::test_sequence_broken_returns_false     PASSED
tests/unit/test_expect_process.py::test_no_trace_file_all_false           PASSED
tests/unit/test_expect_process.py::test_no_trace_path_none_all_false      PASSED
```

## 落地文件
- `tests/harness/expect_process.py` — `check_process(asserts, trace_path) -> list[Result]`，`Result(type, ok, detail)`
- `tests/unit/test_expect_process.py` — 6 个测试（2 正向集合 + 4 负向场景）

## 设计决策
- `Result` dataclass 与 plan 规定一致（type/ok/detail）
- 断言类型无 `trace_` 前缀（skill_loaded/tool_used/tool_absent/tool_count/sequence）
- trace_path 为 None 或文件不存在 → 每条均 False + "no trace captured"
- `skills_loaded` 读 `input.skill`（非 command），与 trace.py 一致

## 偏离/concern
无。实现与 plan Task 6 描述完全对齐；sequence 松子序列语义与旧 asserts.py trace_sequence 一致。
