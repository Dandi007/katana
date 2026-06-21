# Task 1 Report：三轴 schema + 不变量

## 状态
DONE

## Commit
ed589ff — `feat(harness2): 三轴 schema + ≥1 确定性锚不变量 + 去 stdout_grep`

## pytest 输出
```
tests/unit/test_schema.py::test_three_axis_loads PASSED
tests/unit/test_schema.py::test_invariant_requires_deterministic_anchor PASSED
tests/unit/test_schema.py::test_no_stdout_grep_type PASSED
tests/unit/test_schema.py::test_model_explicit_default PASSED

4 passed in 0.09s
```

## 实现细节

### schema.py（重写）
- `PROCESS_TYPES = {"skill_loaded","tool_used","tool_absent","tool_count","sequence"}` — 无 `stdout_grep`
- `FS_TYPES = {"created","modified","deleted","content","unchanged_outside","script"}`
- `Contract` dataclass：`skill/path/case_id/fixture/requires/prompt/turns/tools/model/timeout/process/filesystem/semantic`（去掉旧 `asserts/verdict/cwd/permission_mode/no_tools/allowed_tools`）
- `_check_axis(entries, allowed, path, axis)`：校验每条 entry 为单 key map 且 key 在 allowed 集合，否则抛 `ContractError`
- 不变量：`process` 和 `filesystem` 均空时抛 `ContractError("...process.*filesystem...")`
- `model` 优先级：`trigger.model` > `KATANA_CONTRACT_MODEL` env > `DEFAULT_MODEL`
- `discover_contracts`：glob `plugins/*/tests/contracts/*.contract.yaml`

### test_schema.py（重写）
严格对应 plan Step 1 四个测试，逐字落地，无改动。

## 偏离 plan 处
无。代码与 plan Step 1（测试）、Step 3（实现）完全一致，逐字落地。
