---
name: using-writing
description: Hook-injected convention layer for the writing plugin. Active when writing_dir is set; governs the write/review gate, where project-specific patterns/opinions live, and the cold-read iron rules.
---

# Using Writing

本项目已启用 writing 工具套件。项目特有的写作知识库位于：`{{WRITING_DIR}}`

子目录说明：
- `{{WRITING_DIR}}/template/` — 按**具体文档种类**组织的「写的脸」脚手架；每份含 ① `## Layout` 字面骨架 ② `## 写作 guide(prompt)`；**结构的唯一 SSoT**，写前实例化。规格见 `writing:readability-check` 的 `references/template-spec.md`
- `{{WRITING_DIR}}/patterns/` — 按文档**类型族**组织的「审的脸」SSoT（机检+冷读 checklist、反模式、适用判定、演进记录）；写的脸已迁往 `template/`，未迁移的类型暂在 pattern 内保留写法骨架作回退
- `{{WRITING_DIR}}/improvements/` — 意见演进卡池（每张卡含 `状态: active|superseded`，检索时只读 active）
- `{{WRITING_DIR}}/staging/` — 原始反馈 inbox（immutable，进化前的暂存区）

## 路由表

按意图路由，语言不限。

| 意图 | skill |
|------|-------|
| 写 spec/方案/会议纪要/日报/周报/汇报/README/邮件/知识库说明文档（起草·改写·润色·重组） | `writing:write` |
| 检查一份文档好不好读 / 能否脱离上下文独立看懂 / 是否符合该类型写作规范 / 把 review 意见固化成长期规则 | `writing:readability-check` |
| 信息分层 / BLUF L0-L3 结构 / 去 AI 味反模式（写审共享结构 SSoT） | `writing:bluf` |

## 铁律

1. **写前先实例化 template，再读 pattern + active improvements。** 起草或改写任何文档前：识别具体 kind → 若 `{{WRITING_DIR}}/template/<kind>.md` 存在，按其 `## Layout` 实例化骨架、依 `## 写作 guide` 填；再读 `{{WRITING_DIR}}/patterns/<type>.md` 的适用判定/反模式 + `improvements/` 中 `状态: active` 的卡。**无 template 命中** → 回退读 pattern 内残留写法骨架，并可 offer `/readability-check distill` 起一份。写后执行固定格式自检（清单在 `writing:write` 内）。

2. **冷读必须用 subagent，主 agent 不可信。** 可读性审视的冷读环节主 agent 自带上下文，结论失真；subagent 必须在 prompt 层写明具体禁读文件名和阅读者角色——这是 prompt 层强约束，不是工具白名单。

3. **进化永远人工 gate，防 model-collapse。** raw 反馈 immutable，先进 `{{WRITING_DIR}}/staging/`；compiled 意见经用户确认后才写回 `template/`（结构/写法）+ `patterns/` + `improvements/`（评判）。model 不得自行将 staging 内容升格为 active。

4. **本 plugin 只管写作与可读性，不越界。** 事实核对 → `verify`；论证质疑 → `critical-review`；去 AI 味 → `humanizer-zh` / `humanizer`。指路，不代劳。

5. **所有项目特有产物归一到 `{{WRITING_DIR}}`。** plugin 内部不自建平行库；plugin 提供引擎，项目 writing_dir 提供数据。
