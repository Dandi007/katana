# Task 8 报告：轴③ 可插拔 judge

## status
DONE

## commit
55e74bf  feat(harness2): 轴③ 可插拔 judge（SingleJudge 默认 + JuryJudge stub）

## pytest 汇总
```
6 passed in 0.04s

tests/unit/test_judge.py::test_parse_fenced_json PASSED
tests/unit/test_judge.py::test_case_verdict_all_yes_passes PASSED
tests/unit/test_judge.py::test_case_verdict_any_no_needs_review PASSED
tests/unit/test_judge.py::test_judge_failure_is_needs_review PASSED
tests/unit/test_judge.py::test_get_judge_jury_stub PASSED
tests/unit/test_judge.py::test_get_judge_unknown_raises PASSED
```

## 实现说明

### judge.py 重写
- `SingleJudge.judge(rubric, inputs, model, work_dir, env, claude_bin) -> (status, dict)`
  - 搬旧 `run_case_verdict` 逻辑；改调 `trigger.run`（不再依赖已废弃的 `claude_cli.run_claude`）
  - 任何非 yes 项 → NEEDS-REVIEW；解析/执行异常也 NEEDS-REVIEW
- `JuryJudge.judge(...)` → `raise NotImplementedError("jury adapter: 后续 MR")`
- `get_judge(name)` registry `{"single": SingleJudge(), "jury": JuryJudge()}`，未知名 raise KeyError
- `parse_verdict_json` 保留为公共函数（fenced json 提取）
- 保留向后兼容顶层函数 `run_case_verdict`（委托 SingleJudge，旧代码可继续 import）

### test_judge.py 重写
- 4 条旧测试改走 `get_judge("single").judge(...)`（接口签名：`env=` 替换旧 `base_env=`）
- 新增 `test_get_judge_jury_stub`：验 NotImplementedError + match "jury adapter"
- 新增 `test_get_judge_unknown_raises`：验未知名 KeyError

## 偏离 / concern
- **无功能性偏离**。
- trigger.run 的 `result_text` 字段（stream-json 末条 result 事件）替换了旧 `run_claude` 的 `res.stdout`；fake-claude shim 已按 stream-json 模式吐 result 事件，测试通过验证该路径正确。
- 旧顶层 `run_case_verdict` 函数向后兼容保留（plan 未要求删，旧测试文件 test_case.py / test_claude_cli.py 等有 import，暂不动）。Task 15 退役旧文件时可一并清理。
