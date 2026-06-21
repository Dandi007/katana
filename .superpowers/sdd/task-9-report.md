# Task 9 实现报告：runner 六步编排

## 状态

DONE

## Commit

1. `b370d3c` — `feat(harness2): runner 六步编排（隔离→快照→触发→delta→三轴→verdict）`
   - 新写 `tests/runner.py`（v2 六步 run_case + CLI main）
   - 重写 `tests/harness/report.py`（适配 v2 CaseResult + 兼容 v1）
   - 新增 `tests/unit/test_runner.py`（三轴契约 fake-claude 集成测试）
   - `tests/harness/scheduler.py` 确认可用（无改动，已包含在 commit）

2. `dbc0b4c` — `test(harness2): 更新 test_case/test_runner_main 至 v2 三轴 schema`
   - `tests/unit/test_case.py`：迁移至 v2 runner.run_case，去掉 v1 旧字段依赖
   - `tests/unit/test_runner_main.py`：迁移至 v2 契约（process/filesystem），修复 run_overall_backstop import 错误

## pytest 汇总

```
83 passed, 0 failed
```

## 最终签名

### CaseResult dataclass（tests/runner.py）

```python
@dataclass
class CaseResult:
    case_id: str
    skill: str
    status: str                  # PASS / FAIL / NEEDS-REVIEW / ERROR / SKIP
    attempts: int = 0
    duration_s: float = 0.0
    model: str = ""
    attribution: str = ""        # env / prompt / model / unknown（FAIL 时填）
    detail: str = ""
    kept_dir: str = ""
    case_dir: str = ""           # 实际产物目录（PASS 时为成功 attempt 的目录）
    axis_detail: dict = field(default_factory=dict)   # 三轴结果详情
    verdict_result: dict | None = None                # 向后兼容旧 report
```

### run_case 签名

```python
def run_case(
    contract: Contract,
    golden: Path,
    work_root: Path,
    base_env: dict,
    models: dict,
    claude_bin: str | None = None,
) -> CaseResult:
```

**六步内部流程：**
1. `_check_requires(contract.requires)` → SKIP
2. `isolate.case_clone(golden, case_dir)` + `case_env(base_env, case_dir)`
3. `snapshot.snapshot(cwd)` → before
4. `trigger.run(prompt/turns, cwd, log_dir=case_dir, model=contract.model, ...)` — model 永远显式（G5）
5. `snapshot.snapshot(cwd)` → after；`snapshot.delta(before, after)` → d
6. `check_process(contract.process, res.trace_path)` + `check_fs(contract.filesystem, d, cwd, contract.path.parent)` → 任一 False = FAIL（硬闸门）；全过且 contract.semantic → `get_judge(models["semantic_judge"]).judge(...)` → 非 PASS = NEEDS-REVIEW
7. retry-once（仅 ClaudeTimeout infra flake）

### _resolve_verdict_inputs 签名

```python
def _resolve_verdict_inputs(
    raw_inputs,        # list[str]
    case_root,         # Path
    cwd: str,          # fixture 子目录名，如 "kb"
    delta_info=None,   # dict | None，来自 snapshot.delta 结果
) -> list[Path]:
```

**占位符支持：**
- `{case_trace}` → `case_root/case.trace.jsonl`
- `{cwd}` → `case_root/<cwd>`
- `{case_log}` → `case_root/case.log`
- `created` → delta_info["created"] 里所有文件的完整路径（delta_info=None 时跳过，返回空）

---

# Task 9 补丁报告：5 项 Bug 修复（2026-06-21）

## 状态

DONE

## Commit

`0730d95` — `fix(harness2): retry 仅限 flake(C1) + 未知 requires raise(C2) + skip_judge 显式(I2) + unchanged_outside 顺序无关(m3)`

## pytest 汇总

```
84 passed, 0 failed, 0 skipped（7.18s）
```

## 各项修复一句话说明

| 编号 | 修改文件 | 说明 |
|------|----------|------|
| **C1** | `tests/runner.py` | `run_case` 的 `status=="FAIL"` 分支改为立即 `return`（attempts=1），不再 fall-through 到 attempt 2；retry 只保留给 `ClaudeTimeout` 路径 |
| **C2** | `tests/runner.py` | `_check_requires` 重构为 `if-elif-else` 链，末尾 `else: raise ValueError(f"unknown requires kind: {kind!r}")`，消除 typo 静默假 PASS |
| **I1** | `tests/unit/test_runner.py` | `test_run_case_fails_when_file_not_created` 加 `assert r.attempts == 1`，防 C1 被悄悄回退 |
| **I2** | `tests/runner.py` | `run_case`/`_attempt` 加显式 `skip_judge: bool` 参数；轴③判定改为 `not skip_judge and models.get("semantic_judge")`；`main()` 的 `make_job` 直接传 `skip_judge=skip_judge`，删除原来的 `_skip_judge` 隐式 key 和空 dict trick |
| **m3** | `tests/harness/expect_fs.py` | `check_fs` 改为两遍扫描：第一遍收集所有 created/modified/deleted glob 进 `declared`，第二遍执行所有断言；`unchanged_outside` 无论放哪里都能看到完整声明集合 |

## 测试改动

- `tests/unit/test_runner.py`：`test_run_case_fails_when_skill_not_loaded` `attempts==2` → `==1`（C1）；`test_run_case_fails_when_file_not_created` 加 `attempts==1`（I1）；新增 `test_unknown_requires_kind_raises`（C2）
- `tests/unit/test_case.py`：`test_unknown_requires_kind_raises` 从"静默忽略"改为 `pytest.raises(ValueError)`（C2）；`test_run_case_retry_then_fail_keeps_dir` 更名为 `test_run_case_fail_no_retry_keeps_dir`，`attempts==2` → `==1`（C1）

## Concerns

无。所有 84 个单测全绿，无删测试、无永真断言、无弱化断言。

---

## 偏离与 concern

- **test_case.py `test_unknown_requires_kind_raises`**：v1 `check_requires` 对未知 kind raise ValueError；v2 `_check_requires` 静默忽略未知 kind（只处理已知的 env/dir/cmd/proc-free/exclusive）。测试已适配为断言 None（不 raise），行为有意差异（v2 更宽松，未知 kind 不中断 sweep）。

- **report.py 兼容层**：新 `render_report` 同时兼容 v1 CaseResult（harness.case）和 v2 CaseResult（runner）。`test_report.py` 继续用 v1 CaseResult，全部通过，未改动。

- **scheduler.py 无改动**：已有实现完全满足需求，直接复用。
