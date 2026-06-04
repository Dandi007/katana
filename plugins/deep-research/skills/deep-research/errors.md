# deep-research Errors

记录本 skill 已知坑与回归，执行前先读。

## 已知坑

1. **Workflow `args` 到脚本里是 JSON 字符串不是对象** → workflow.js 必须防御式 `JSON.parse`（已内置，勿删）。

2. **worker agentType 不能用 `Explore`**：Explore 只读无 Write，worker 要写 L2 findings 文件 → 统一 `general-purpose`（已修，勿回退）。

3. **弱模型 L2 落盘纪律不稳**：L1（schema 返回）可靠，但部分轮次 worker 不写 findings 文件（弱模型实测示例：qwen3.7-max，89 条 L1 findings 只落了 11 个 L2 文件，r3-5/r7 缺失）。综合 agent 只能用已落盘的素材。强模型预计更稳；弱模型跑时可接受或在 worker prompt 加"写完用 Read 验证文件存在"硬化。

4. **弱模型 depth 上报全为 1**：depth 语义是"与原问题的语义距离"（判断层），弱模型偷懒全报 1 → 深度护栏实际不触发。收敛仍由 triage `converged` 判断兜底，未失控（7 轮自然停）。

5. **主题目录名可能被起成英文**：pre-phase 由模型起名，弱模型可能使用英文目录名（如"Claude Code Multi Agent Orchestration"）。介意时在调用前显式给定中文主题名。

6. **headless `claude -p` 单轮会等 Workflow 完成**（不孤儿化），可作 CI/验收载体。headless 验收时建议加 `--dangerously-skip-permissions`（无人值守场景权限询问会卡住流程）。
