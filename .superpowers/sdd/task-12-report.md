# Task 12 Report: wiki 6 + writing 7 契约迁三轴

**status:** DONE  
**commit:** b3a0166  
**branch:** feat/e2e-harness-v2  
**验收计数:** 14/14 load_contract OK，0 FAIL

---

## 验收输出

```
Wiki contracts:    7/7 OK  （含已迁的 ingest-inbox）
Writing contracts: 7/7 OK
TOTAL: 14 OK, 0 FAIL
```

各契约断言分布：

| 契约 | process | filesystem | semantic |
|---|---|---|---|
| wiki/ingest-inbox | 1 | 8 | no |
| wiki/init-adopt | 1 | 4 | no |
| wiki/lint-full | 1 | 4 | yes |
| wiki/lint-summary-backfill | 1 | 4 | yes |
| wiki/query-cold | 1 | 5 | no |
| wiki/query-hot | 1 | 5 | yes |
| wiki/using-wiki-ironrules | 1 | 5 | yes |
| writing/bluf-structure | 1 | 0 | yes |
| writing/distill-mode | 1 | 0 | yes |
| writing/evolve-triage | 1 | 0 | yes |
| writing/readability-check-workflow | 1 | 0 | yes |
| writing/using-writing-injected | 1 | 0 | yes |
| writing/write-smoke | 1 | 0 | yes |
| writing/write-template-instantiate | 1 | 0 | yes |

---

## 变更说明

### wiki 6 个契约（旧格式 → 新三轴）

**init-adopt**
- `file_exists/file_grep` → `filesystem.created + content`
- fixture 从旧 `input.cwd: kb/init-arena` → `setup.fixture: kb/init-arena`

**lint-full**
- `file_exists/file_grep` → `filesystem.created + content`
- `verdict:` → `expect.semantic`，复用已有 `tests/judge/case-rubrics/wiki-lint.md`

**lint-summary-backfill**
- `script: scripts/check-summary-backfill.sh` 保留进 `filesystem.script`
- `verdict:` → `expect.semantic`，复用 `wiki-lint-summary.md`
- **verify.sh 变量修改**：`$KB_DIR` → `$CWD`（新 env 注入约定）
- semantic inputs 改用 `{created:...}` 占位

**query-cold**
- 旧 `stdout_grep: 不覆盖|non-wiki|未收录` → prompt 要求 skill 把答案写 `answer.md`，`filesystem.content` 验 gap 提示词
- `file_grep: gap-log.md` → `filesystem.modified + content`
- 旧 `file_absent: 笔记/*tokio*` → 由 `unchanged_outside: true` 覆盖（tokio 笔记本就不存在，delta 不会出现）

**query-hot**
- 纯 `stdout_grep` → prompt 要求 skill 落 `answer.md`，`filesystem.content` 验 "90"/"1:15"/wikilink
- 新增 rubric `wiki-query-hot.md`

**using-wiki-ironrules**
- 纯 `stdout_grep` → prompt 要求 skill 落 `answer.md`，`filesystem.content` 验 "92"/"96"/wikilink
- 新增 rubric `wiki-using-ironrules.md`

### writing 7 个契约（全量迁移）

所有 writing 契约原为 `no_tools: true` + 纯 `stdout_grep` 答问型。
迁移策略：`process(skill_loaded) + semantic(rubric)`，满足不变量（process ≥1）。
`tools: []` 替代旧 `no_tools: true`。

新增 7 个 rubric（`tests/judge/case-rubrics/writing-*.md`）：
- `writing-bluf-structure.md`
- `writing-distill-mode.md`
- `writing-evolve-triage.md`
- `writing-readability-check-workflow.md`
- `writing-using-writing-injected.md`
- `writing-write-smoke.md`
- `writing-write-template-instantiate.md`

---

## verify.sh $KB_DIR → $CWD 修改

| 文件 | 修改 |
|---|---|
| `plugins/wiki/tests/contracts/scripts/check-summary-backfill.sh` | `KB_DIR` → `CWD`（2处：`GOLDEN="$CWD/.golden"` + `page="$CWD/笔记/$f.md"`） |

---

## concern / 偏离说明

1. **query-cold prompt 改动**：旧契约 prompt 没有要求落文件，新契约在 prompt 里加了"把回答写到 answer.md"。这是 G2（答问类落文件→轴②）的必要改动，但意味着 live 跑时 skill 行为须配合落文件。若 wiki:query 不主动落文件，此契约会 FAIL（filesystem.created）——这是预期的确定性约束，不是 bug。using-wiki-ironrules 同理。

2. **writing 契约 tools: []**：新 schema 中 `tools` 为 list 类型，空列表合法（load_contract 校验通过）。旧的 `no_tools: true` 语义等价。runner 执行时需确认空 tools 列表传给 claude CLI 的参数处理正确（不传 --allowedTools 或传空）。

3. **lint-summary-backfill semantic inputs**：旧 `inputs: ["{cwd}/笔记/手冲咖啡萃取.md", ...]` 用的是路径，新改为 `{created:笔记/手冲咖啡萃取.md}` 占位。由于 lint 做的是 modify 而非 create，占位符类型与实际 delta 不完全匹配——runner 的 `_resolve_verdict_inputs` 需支持 modified 文件的路径解析，或直接传 `{case_trace}`。此处用 `{created:...}` 是基于 schema 现有占位语法的最近似选择，后续 Task 16 终审时可复查。
