# deep-research Errors

记录本 skill 已知坑与回归，执行前先读。

## 已知坑

1. **Workflow `args` 到脚本里是 JSON 字符串不是对象** → workflow.js 必须防御式 `JSON.parse`（已内置，勿删）。

2. **worker agentType 不能用 `Explore`**：Explore 只读无 Write，worker 要写 L2 findings 文件 → 统一 `general-purpose`（已修，勿回退）。

3. **弱模型 L2 落盘纪律不稳**：L1（schema 返回）可靠，但部分轮次 worker 不写 findings 文件（弱模型实测示例：qwen3.7-max，89 条 L1 findings 只落了 11 个 L2 文件，r3-5/r7 缺失）。综合 agent 只能用已落盘的素材。强模型预计更稳；弱模型跑时可接受或在 worker prompt 加"写完用 Read 验证文件存在"硬化。

4. **弱模型 depth 上报全为 1**：depth 语义是"与原问题的语义距离"（判断层），弱模型偷懒全报 1 → 深度护栏实际不触发。收敛仍由 triage `converged` 判断兜底，未失控（7 轮自然停）。

5. **主题目录名可能被起成英文**：pre-phase 由模型起名，弱模型可能使用英文目录名（如"Claude Code Multi Agent Orchestration"）。介意时在调用前显式给定中文主题名。

6. **headless `claude -p` 单轮会等 Workflow 完成**（不孤儿化），可作 CI/验收载体。headless 验收时建议加 `--dangerously-skip-permissions`（无人值守场景权限询问会卡住流程）。

7. **Skill 层撞名**：宿主环境可能装有其他同名 `deep-research` skill（如 web 对抗验证 harness）。裸 `/deep-research` 可能路由到别家——**调用必须用全限定名 `/deep-research:deep-research`**。判别走错：报告只到 stdout、无 `DeepThought/<主题>/` 落盘、出现"候选→验证→击杀"话术（2026-06-04 实测）。

8. **Workflow 层撞名**：即使 Skill 路由正确，阶段 B 若用 `Workflow({name: "deep-research"})` 仍会被环境里同名 named workflow 劫持——阶段 A 正常建目录、报告也会生成，但 clue_board / findings L2 / sources / topics 全缺，平台源证据丢失（2026-06-04 实测）。必须 `scriptPath` 指向本 skill base directory 的 workflow.js（SKILL.md 已加 MUST 硬约束）。

9. **wiki-v3 cutover 后 skill 不可用（2026-08-28 实测）**：skill 0.6.2 的 Stage A / workflow.js 依赖 wiki MCP `fs_create/fs_glob/fs_read/fs_write` 往 `DeepThought/<主题>/` 写产物，但 2026-08-27 起 katana-wiki-mcp 由 wiki-v3 服务实现，**不再暴露任何 `fs_*` 工具**（只有 search/page_*/repo_write_file 等），且 `DeepThought/` 未迁入新库、旧 `/data/wiki` 已定位为只读归档。Setup 阶段即断。修复方向：skill 产物落位改到 work-folder MCP（有完整 `fs_*`）或新 wiki 的 page 原语。当前 workaround：不跑本 skill workflow，主 agent 自建 subagent fan-out，产物落 work folder。
   → 已修复：0.7.0 起产物落位迁 work-folder MCP（fs_create/fs_write/fs_list + wf_create），wiki 域仅作检索源（search/page_get）。本条保留作历史。
