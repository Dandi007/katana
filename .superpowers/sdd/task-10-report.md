# Task 10 Report：试点 3 契约迁三轴

**status:** DONE  
**commit:** fda24ff  
**branch:** feat/e2e-harness-v2  
**date:** 2026-06-21

---

## 校验方式

`--validate-only` 因旧契约（未迁移的 37 个）使用旧 schema（`input:`/`assert:` 结构，无 `trigger:`），导致 `discover_contracts` 全量加载时 `ContractError: missing trigger.prompt`。

因此使用单文件 Python 片段逐个 `load_contract()` 校验这 3 个文件：

```bash
cd /Volumes/Data/code/worktrees/katana/e2e-v2 && uv run --with pyyaml python3 - <<'EOF'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("tests").resolve()))
from harness.schema import load_contract
for t in ["plugins/wiki/...", "plugins/retrieval/...", "plugins/work-folder/..."]:
    c = load_contract(Path(t)); print(f"OK  {c.case_id}")
print("3 contracts valid")
EOF
```

**输出：** `OK ingest-inbox` / `OK search-note-local` / `OK checkpoint-save` / `3 contracts valid`（无异常）。

---

## 3 契约最终内容与三轴断言

### 1. wiki:ingest（样板 a：filesystem delta）

**文件：** `plugins/wiki/tests/contracts/ingest-inbox.contract.yaml`

**fixture 文件名确认：**
- 源文件：`tests/fixtures/kb/inbox/cold-brew-source.md`（存在，内容含"冷萃咖啡"）
- 预期生成：`笔记/*冷萃*`、`raw/cold-brew-source*`

**三轴：**

| 轴 | 断言 |
|---|---|
| process | `skill_loaded: wiki:ingest` |
| filesystem | `created: 笔记/*冷萃*` / `created: raw/cold-brew-source*` / `deleted: inbox/cold-brew-source.md` / `modified: 笔记/INDEX.md` / `content: 笔记/INDEX.md matches 冷萃` / `modified: wiki-log.md` / `unchanged_outside: true` |
| semantic | 无 |

---

### 2. retrieval:search-note（样板 b：旧 stdout_grep → 落文件 → filesystem.content）

**文件：** `plugins/retrieval/tests/contracts/search-note-local.contract.yaml`

**fixture 文件名确认：**
- `tests/fixtures/kb/笔记/意式浓缩温度-A.md`（存在）
- `tests/fixtures/kb/笔记/意式浓缩温度-B.md`（存在）

**迁移决策：** 旧 `stdout_grep: 意式浓缩温度-A/B` → prompt 改为要求 skill 把命中写入 `./search-result.md` → filesystem `created: search-result.md` + `content: matches 意式浓缩温度-A/B`。这是 G2 闭环的落地：答问类结果落文件转轴② 确定性断言，消灭 stdout_grep。

**三轴：**

| 轴 | 断言 |
|---|---|
| process | `skill_loaded: retrieval:search-note` |
| filesystem | `created: search-result.md` / `content: search-result.md matches 意式浓缩温度-A` / `content: search-result.md matches 意式浓缩温度-B` / `unchanged_outside: true` |
| semantic | 无 |

---

### 3. work-folder:checkpoint（样板 c：多轮 turns + filesystem created/modified）

**文件：** `plugins/work-folder/tests/contracts/checkpoint-save.contract.yaml`

**fixture 文件名确认：**
- `tests/fixtures/kb/工作记录/fixture-task/progress.md`（存在，status=execution）
- `tests/fixtures/kb/工作记录/fixture-task/CLAUDE.md`（**不存在**，由 skill 创建 → `created` 断言正确）

**多轮设计：**
- Turn 1：指示 skill 执行 checkpoint save（保存矛盾调研结论）
- Turn 2：验证确认（"刚才的 checkpoint 完成了吗？简述 progress.md 里记录了什么内容。"）

**旧契约无 turns，本次新增多轮**（plan 样板 c 说"保留多轮 turns"是针对迁移后的目标形式，旧契约为单轮单 prompt）。

**三轴：**

| 轴 | 断言 |
|---|---|
| process | `skill_loaded: work-folder:checkpoint` |
| filesystem | `modified: 工作记录/fixture-task/progress.md` / `content: progress.md matches 矛盾调研` / `created: 工作记录/fixture-task/CLAUDE.md` / `content: CLAUDE.md matches Resume` / `unchanged_outside: true` |
| semantic | 无 |

---

## Concern / 偏离

1. **`--validate-only` 不能直接用于全量**：旧契约用旧 schema（`input:` 顶层 key），`discover_contracts` 在加载它们时抛 ContractError。解法是逐文件 `load_contract()` 校验，或等 Task 11–14 全量迁完后才能跑全量 `--validate-only`。本次按 plan 指引采用逐文件校验方式，已在 report 中说明。

2. **checkpoint-save 旧契约无 turns**：plan 样板 c 说"保留多轮 turns"，但旧契约实际为单轮。本次根据 plan 意图**新增多轮 turns**（save + verify 两轮），是功能升级而非保留。

3. **unchanged_outside 放最后**：3 个契约均已遵守（放 filesystem 列表末尾），符合 plan 约定（expect_fs.py 依赖 declared 累积顺序）。

4. **tests/judge/ 未新增文件**：3 个契约均无 semantic 轴（无 rubric 引用），judge/ 目录无需改动。
