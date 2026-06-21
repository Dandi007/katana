---
name: review
description: 用多个不同大模型并行 review 当前分支的代码改动（本地 git diff），保留各模型分歧并给出投票裁决。当用户想对一个写好的改动/PR 做多模型交叉评审时使用。
---

# jury — 多模型并行评审

对当前 git 仓库的改动做**多模型交叉评审**：同一份评审 rubric 并行打给 N 个不同模型（默认 Opus 原生 + GPT-5.5 + DeepSeek-V4-Pro + Qwen），保留各模型分歧，给出投票裁决。**不合并、不调和分歧**——分歧本身是信号。

## 执行流程

1. **确定 base**：用户给了就用；否则 `git merge-base main HEAD`（无 main 用 `origin/HEAD`）。
2. **取 diff**：`git diff <base>...HEAD`。空 diff 则告知用户无改动、停止。
3. **拼 prompt**：读模板 `${CLAUDE_SKILL_DIR}/../../templates/review-rubric.md`，把 diff 追加到模板末尾的 ```diff 代码块里，写到临时文件 `<repo>/.jury/prompt.md`。
4. **跑引擎**：
   ```bash
   "${CLAUDE_SKILL_DIR}/../../engine/panel.py" --prompt-file <repo>/.jury/prompt.md --out <repo>/.jury
   ```
   引擎并行扇出、落三产物到 `<repo>/.jury/`。
5. **汇报**：读 `<repo>/.jury/jury-verdict.json`，向用户呈现：每项多数决 + dissent（哪些项模型有分歧）+ 指向 `jury-report.md` 看各模型原文。**不替模型下结论**，分歧如实呈现。

## 约束

- `.jury/` 目录加进 `.gitignore`（运行时产物，不入库）。
- 引擎需要 ccs（灵智三路）在线 + 本机 Claude 订阅（Opus 原生路）。某路挂 → 引擎记 partial quorum，照常出剩余模型结果。
- 你的角色是**编排 + 如实转述**，不是第 N+1 个评审者；不要把自己的判断混进投票。
