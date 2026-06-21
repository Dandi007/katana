# Task 4 报告：trigger.py + trace.py 搬运

## status
DONE

## commit sha
6a1b62e

## pytest 汇总
```
14 passed in 1.97s
tests/unit/test_trigger.py  11 passed
tests/unit/test_trace.py     3 passed
```

## trace.py 是否改动
**未改动**。现有 `tests/harness/trace.py` 已正确读 `input.skill`（`skills_loaded` 从 Skill tool_use 的 `b.get("input", {}).get("skill", "")` 提取），与真实 schema 吻合，无需修改。

## 交付文件
| 文件 | 状态 |
|------|------|
| `tests/harness/trigger.py` | 新建 |
| `tests/harness/trace.py` | 已存在，未改动 |
| `tests/unit/test_trigger.py` | 新建（11 个用例） |
| `tests/unit/fake-claude` | 已存在，完整支持 stream-json / --resume，无需改动 |

## trigger.py 主要机制
- `Result(result_text, trace_path, exit_code)` dataclass
- 单入口 `run(prompt|turns, cwd, log_dir, model, tools, timeout, env, claude_bin)`，内部分支单轮/多轮
- `claude -p --model <model> --output-format stream-json --verbose`，prompt 走 stdin
- 超时 `killpg(start_new_session)` → `ClaudeTimeout`
- trace 写 `log_dir/case.trace.jsonl`；多轮中间轮写 `case{N}.trace.jsonl`，末轮拷贝到 canonical
- `_result_text` 取末条 result 事件的 result 字段
- 多轮用 `_parse_session` 提取 session_id，第二轮起加 `--resume`
- prompt/turns 互斥校验（两者皆传或皆空 → ValueError）

## 偏离 / concern
无偏离。fake-claude 已完整支持 stream-json + --resume，直接复用；trace.py 无需动；plan Step1/Step2 合并为"确认不改动"。
