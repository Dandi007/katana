# Task 10 修复报告

**时间**：2026-06-21  
**分支**：feat/e2e-harness-v2  
**commit**：53f2d1f

---

## status: DONE

## pytest 汇总

```
12 passed in 1.23s
tests/unit/test_trigger.py — 全部通过（含新增多轮 trace 累积测试）
```

契约 schema 验证：2/2 OK（ingest-inbox + checkpoint-save）

---

## BUG 修复：多轮 trace 累积全轮事件

**根因**：`trigger.py` 多轮分支末尾直接 `canonical_trace.write_text(last_out)` ——  
`last_out` 只是末轮的 stdout，turn1 所有事件全部丢失。

**修法**（`tests/harness/trigger.py` 第 172-178 行）：  
各轮已正确写 `caseN.trace.jsonl`；末尾改为把所有轮文件内容拼接后写入 `case.trace.jsonl`：

```python
all_turns_content = "".join(
    (log_dir / f"case{i}.trace.jsonl").read_text(encoding="utf-8")
    for i in range(len(texts))
)
canonical_trace.write_text(all_turns_content, encoding="utf-8")
```

`result_text` 维持 `"\n".join(texts)` 不变（各轮末条 result 文本拼接）。  
各轮 `caseN.trace.jsonl` 仍写入，供调试对比。

**新测试** `test_multiturn_trace_accumulates_all_turns`：  
- 用 fake-claude 跑 2 轮（每轮都吐 system + Skill tool_use assistant + result 共 3 行）
- 断言 `load_trace` 得到 ≥6 个事件（两轮全量）
- 断言 `skills_loaded` ≥2（turn1 的 skill 事件没有丢失）

---

## 契约欠声明补充

### ingest-inbox.contract.yaml（wiki:ingest）

新增：
```yaml
- modified: "笔记/手冲咖啡萃取.md"
```
位置：放在 `modified: "wiki-log.md"` 之后、`unchanged_outside: true` 之前。  
理由：ingest 处理冷萃咖啡笔记时，会给相关笔记 `手冲咖啡萃取.md` 加 backlink `[[冷萃咖啡]]`，属合法写入，之前未声明导致 `unchanged_outside` 判定为违规。

### checkpoint-save.contract.yaml（work-folder:checkpoint）

新增：
```yaml
- created: "工作记录/fixture-task/context.md"
- created: "工作记录/fixture-task/findings.md"
```
位置：放在 `created: "工作记录/fixture-task/CLAUDE.md"` 之后、`unchanged_outside: true` 之前。  
理由：checkpoint skill 实测会创建 context.md 和 findings.md 作为工作记录产物，之前未声明。

---

## Concerns

无阻塞性问题。以下供参考：

1. **fake-claude 每轮输出相同事件**：当前测试只能断言"两轮事件都在"（≥2 个 Skill），  
   无法区分是来自 turn1 还是 turn2（fake 每轮吐相同结构）。  
   若将来需要精确验证"turn1 的特定事件"，需给 fake-claude 增加按轮次输出不同内容的能力（如读 `FAKE_CLAUDE_TURN_EVENTS` env）。  
   当前测试已足以覆盖 bug 回归（修前 skills=[] 会失败，修后 ≥2 通过）。

2. **契约 `unchanged_outside` 语义**：补声明后若实际运行产生其他未声明文件，  
   仍会触发 unchanged_outside 违规——这是预期行为，不是问题。
