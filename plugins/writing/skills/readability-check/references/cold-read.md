# 冷读 subagent 机制

> 吸收自已退役的 `writing:readability-check` 前身（cold-read-review），适配本环境（`Agent` 工具 + `AskUserQuestion`，而非 `task`/oracle/`question`）。
> 这是 readability-check「冷读 pass」的方法论；具体 prompt 模板见 `cold-read-prompts/`。

## 核心假设

作者写文档时不可避免带入自己的上下文（之前的对话、同事名字、内部编号、相邻文档结论）。这些隐形依赖在**作者自己读**时看不出来，但对**第一次打开文档的读者**就是理解障碍。

## 核心做法

派 subagent 扮演**没有任何前置上下文的冷读者**，硬约束它只能读被检文档本身、不能读其他相关文件，以此暴露所有「只有作者和利益相关方才看得懂」的隐形依赖。

## 为什么必须用 subagent

主 agent 是文档作者（或刚读完一堆上下文），带着全部上下文，它「冷读」不可信。必须把冷读外包给一个上下文干净、被硬约束的 subagent。

## 本环境派发方式

用 `Agent` 工具：

- **单文档**：1 个 agent，prompt 用 `cold-read-prompts/single-doc-prompt.md`。
  - `subagent_type`：默认 general-purpose；文档涉及代码需对照 repo 时用 `Explore`。
- **多文档（一组交付物）**：N 个单文档冷读 + 1 个交叉一致性，**全部在同一响应里 `run_in_background: true` 并行派出**（串行白等）。交叉一致性用 `cold-read-prompts/cross-consistency-prompt.md`。

派发数量：

| 文档数 | 派 agent 数 | 分工 |
|---|---|---|
| 1 | 1 | 单文档冷读 |
| 2 | 3 | 2 单文档 + 1 交叉一致性 |
| 3 | 4 | 3 单文档 + 1 交叉一致性 |
| N≥4 | ≥N+1 | N 单文档 + 1–2 交叉一致性 |

## 必须写进 prompt 的硬约束

1. 「你**没看过**本 work folder / repo / 项目里任何其他文件，只能读 `<目标文件>`」——并**列出具体禁读文件名**（findings.md / golden-order.md / spec.md / plan.md / context.md / progress.md / AGENTS.md / 相邻文档），不要只说"不能读相关文件"。
2. 「你**不知道**作者与用户的历史对话、checkpoint、session summary」。
3. 「引用到外部文件的地方，当作读者打不开它处理」。
4. 「**不要客气、不要鼓励、不要先肯定后否定——直接找问题**」。
5. 「每条问题**精确到行号或段落**」。
6. 「自包含性好就直接说'自包含性良好'并给理由，**不要凑数**」。

## 角色必须具体

不是"一个读者"，而是按文档类型选具体角色（见 single-doc-prompt 的角色变体表）：spec→第一次接手的前端 tech lead / 外部评审；API→第一次对接的 client 开发；runbook→值班 oncall 新人；会议纪要→未参会同事；原子笔记→该领域有基础但不熟本项目的读者。角色影响它关注什么。

## 禁忌

- 不要告诉 subagent「这份文档已多轮修改」——制造 anchoring bias。
- 不要说「review 一下但别太严格」——等于放弃 review。
- 不要让一个 subagent 同时读多份文档——会互相「对齐」，失去冷读价值。
- 不要让一个 subagent 同时做单文档冷读 + 交叉一致性——分开派。

## 返回后

subagent 返回的问题混在一起，主 agent 做二次分级 P0/P1/P2（见 SKILL.md Step 3），合并机检项，再用 `AskUserQuestion` 让用户选修复范围。修完 P0/P1 可选再派一个 subagent 冷读验证修复是否成功、有无引入新问题。
