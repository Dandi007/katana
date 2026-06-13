# write 集成场景

## 目标

验证 `writing:write` 是否已经强接入以下文档类 skill：

- `create-note`
- `compose-work-email`

## RED 基线观察

### Scenario 1: `create-note` 未经过 writing gate

- 观察结果：baseline agent 会先做对象定义、产物路由和已有笔记检索，但不会先进入 `writing:write`
- 证据摘录：`不会先进入 writing:write，也不会先读取改进卡片`
- 结论：说明当前 `create-note` 还没有共享写前记忆和写后反馈闭环

### Scenario 2: `work` 未经过 writing gate（已归档）

- 观察结果：baseline agent 会先确认文档类型、解析主题并准备模板，但不会先读取改进卡片，也没有固定格式 AI 自检
- 证据摘录：`work skill 里没有这两个前置要求`
- 结论：说明旧 `work` 曾是独立文档模板 skill，没有统一 writing memory 入口；该 skill 已 hard delete，不再作为运行入口。

### Scenario 3: `compose-work-email` 未经过 writing gate

- 观察结果：baseline agent 会先理解邮件需求和检索文档，但不会先进入 `writing:write`，也不会沉淀反馈卡片
- 证据摘录：`它没有要求固定格式 AI 自检，也没有要求沉淀反馈卡片`
- 结论：说明邮件写作 workflow 还未纳入共享写作质量闭环

## GREEN 验证

- Scenario: `create-note` 在真正落正文前经过 `writing:write`
  - Expected behavior: 先做产物路由判断；一旦决定写正文，就先进入 `writing:write`，并在写后执行固定格式 AI 自检与反馈卡片沉淀
  - Observed behavior:
    - `create-note` SKILL.md 顶部新增 `writing:write 前置 gate`
    - 创建文件前明确要求"真正写正文前，必须先把目标卡片视作文档写作任务，进入 writing:write workflow"
    - 完成检查新增"是否已经执行 fixed-format AI 自检 / 是否沉淀改进卡片"
    - 共享层没有硬编码"读取另一个 skill 文件"，而是使用平台无关的 workflow 集成表述
  - Pass/Fail: pass
- Scenario: `work` 在正文写作前后接入 `writing:write`（历史验证，入口已删除）
  - Expected behavior: 完成最小文档类型判断后立即进入 `writing:write`；正文完成后回到其写后流程做 AI 自检与反馈沉淀
  - Observed behavior:
    - 共享层真身曾存在（vault work skill）；现已删除
    - Claude/Codex/OpenCode wrapper 都改成极薄入口，分别跳转到共享层
    - 共享层中明确要求"在任何模板选择、正文撰写、关联补全之前，必须立即进入 writing:write 前置 gate"
    - 共享层把集成写成"进入 writing:write workflow（通过当前平台的 skill 加载机制）"，没有硬编码读取另一个 skill 文件
    - 共享层新增"步骤 8：执行写后自检与反馈沉淀"，并显式区分 `improvements/` 与 `errors.md`
  - Pass/Fail: pass
- Scenario: `compose-work-email` 在邮件正文写作前后接入 `writing:write`
  - Expected behavior: 获得最小邮件信息后先进入 `writing:write`，再做文档检索和模板；预览前先做固定格式 AI 自检与反馈沉淀
  - Observed behavior:
    - 共享层真身存在：`compose-work-email` SKILL.md
    - Claude/Codex/OpenCode wrapper 都改成极薄入口，分别跳转到共享层
    - 共享层中明确要求"在任何文档检索、模板选择、正文撰写之前，必须立即进入 writing:write 前置 gate"
    - 共享层把集成写成"进入 writing:write workflow（通过当前平台的 skill 加载机制）"，没有硬编码读取另一个 skill 文件
    - 共享层中明确要求"给用户预览前，必须先执行 writing:write 的写后流程"
  - Pass/Fail: pass

# References

- source_type: llm, credibility: medium, 本次会话中对 `create-note` / 旧 `work` / `compose-work-email` 的 RED 基线测试结果
