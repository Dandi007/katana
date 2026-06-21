# Task 14 报告：work-folder 剩 1 + jury 1 契约迁三轴

**status:** DONE  
**commit:** 6c6e2d1  
**branch:** feat/e2e-harness-v2  
**时间:** 2026-06-21

---

## 校验结果

3 个契约全部通过 Python 校验脚本：

```
OK  review-smoke.contract.yaml
OK  checkpoint-resume.contract.yaml
OK  checkpoint-save.contract.yaml

Total contracts: 3
Errors: 0
All contracts PASS
```

---

## 契约改动明细

### 1. checkpoint-resume.contract.yaml（work-folder）

**旧格式问题：** 使用 `input:/assert:` 旧 schema + `stdout_grep` 验内容（`"咖啡萃取知識頁"`、`"意式浓缩"`）——违反 G2 + 无法迁到三轴不变量。

**迁移策略（类型 b 变体：落文件转 filesystem.content）：**
- 第二轮 prompt 新增要求：让 agent 把读到的 Goal 写入 `./resume-summary.txt`
- `expect.process`: `skill_loaded: work-folder:checkpoint`
- `expect.filesystem`:
  - 保留原有的 `modified/content`（progress.md 矛盾调研）+ `created/content`（CLAUDE.md Resume）
  - 新增 `created: resume-summary.txt` + `content` 匹配 Goal 原文（`整理咖啡|咖啡萃取知识页`）
  - `unchanged_outside: true` 放最后
- 满足确定性锚不变量（process ≥1 + filesystem ≥1）

### 2. review-smoke.contract.yaml（jury）

**旧格式问题：** 混用 `file_exists/size_min/trace_skill_loaded/trace_tool_used/script/verdict` 白盒字段——全属旧 schema，无三轴结构。

**三轴最终断言：**

```yaml
expect:
  process:
    - skill_loaded: jury:review
    - tool_used: Bash
  filesystem:
    - created: "scratch/.jury/jury-report.md"
    - created: "scratch/.jury/panel-meta.json"
    - created: "scratch/.jury/jury-verdict.json"
    - script: review-smoke.verify.sh
  semantic:
    rubric: jury-smoke.md
    inputs:
      - "{case_trace}"
```

**设计决策：**
- 不加 `unchanged_outside`：jury 在 scratch/ 内操作，产物路径动态（git objects 等），加了会误 FAIL
- `size_min` 去掉：改用 `script` 逃逸口做结构性验证（panel-meta.json 四模型路由保真）
- `inputs` 只留 `{case_trace}`（去掉旧的 `jury-report.md` 路径——rubric 已足够从 trace 判断 skill 路由正确性）
- `setup.requires` 保留 `cmd:claude` + `env:KATANA_E2E_JURY` 门控

### 3. review-smoke.verify.sh

**唯一改动：** `$KB_DIR` → `$CWD`

新 harness `expect_fs.py` 的 `script` 分支注入的 env 是 `CWD`（case cwd），旧 `$KB_DIR` 是旧 runner 的 env 变量，新 harness 不注入。

---

## 偏离/concern

1. **checkpoint-resume 第二轮 prompt 改写**：旧 prompt 只问"汇报 Goal/Phase/下一步"，不写文件，无法转 filesystem 断言。改写后要求 agent 写 `resume-summary.txt`。这改变了 skill 的调用场景，但保留了 resume 语义的核心验证（读 progress.md 并汇报 Goal），且更符合 E2E 可确定性验证的原则。

2. **jury semantic inputs 缩减**：旧 `verdict` 的 inputs 包含 `jury-report.md` 文件路径，新 semantic.inputs 只用 `{case_trace}`。理由：rubric（jury-smoke.md）验的是"各模型独立意见保留 + 针对 diff 内容"，这从 trace 就能判断；report.md 是产物验收（filesystem 已覆盖），重复喂 judge 无必要。若后续发现 judge 需要 report 内容，可补 `"{scratch/.jury/jury-report.md}"` 占位。
