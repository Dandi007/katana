# write 压力场景

## 用途

这个文件用于记录 `writing:write` 的 RED/GREEN 验证场景，先证明无专用 skill 时的自然偏移，再验证共享 skill 是否真正建立了"写前读记忆、写后自检、反馈卡片化沉淀、错误分流"的闭环。

## Scenario 1: 收到文档反馈后只修正文稿

- Failure mode: 只修当前文档，不沉淀长期可复用规则
- Input fixture:
  - 文档类型：`spec`
  - 初稿问题：开头空泛，缺少结论先行
  - 用户反馈：希望后续同类文档都避免这个问题
- Baseline prompt:

```text
你在维护一个知识库。请先写一小段 spec 开头，主题是"统一文档写作 skill"。

然后模拟收到这条用户反馈：
"这段开头太空了，没有先讲结论。你这次改掉，同时以后写 spec 也别再这样。"

请直接完成你认为合理的处理。
```

- RED expectations:
  - 只修改当前 spec 文本
  - 不沉淀长期规则
  - 不产生独立反馈卡片
- GREEN pass criteria:
  - 先修正文档
  - 再把反馈沉淀为单独改进卡片
  - 卡片中同时包含原始反馈与抽象后的改进原则

## Scenario 2: 开写前不读取历史改进记忆

- Failure mode: 忽略历史 `active` 卡片，直接开写
- Input fixture:
  - 文档类型：`meeting-notes`
  - 历史卡片规则：会议纪要必须先给结论与决策，再写讨论过程
- Baseline prompt:

```text
请帮我写一份 meeting notes，主题是"统一 writing skill 的设计讨论"。

你已知有一条历史改进经验：
"会议纪要不要上来铺陈背景，先写结论、决策和 action items。"

请直接产出你认为合适的结果。
```

- RED expectations:
  - 未先提取历史规则
  - 未生成写前 checklist
  - 直接按通用模板开写
- GREEN pass criteria:
  - 写前先显式提炼历史规则
  - 把规则纳入本次 checklist
  - 产出结构先结论、后讨论，并在写后自检验证是否符合规则

## Scenario 3: 文档反馈和机制错误混记

- Failure mode: 改进反馈与 skill 机制错误没有分流
- Input fixture:
  - 文档类型：`email`
  - 文档反馈：邮件太长、主诉求不够前置
  - 机制错误：本应写入 `errors.md` 的问题被误导成写入改进卡片
- Baseline prompt:

```text
请处理这个写作任务：

1. 有一封工作邮件需要修改，反馈是"邮件太长，主诉求不够前置"。
2. 同时系统里还有一个流程异常：某次写作流程把机制错误也记成了写作改进意见。

请你统一处理，并输出你认为该如何记录这些信息。
```

- RED expectations:
  - 把两类问题混记到一处
  - 不区分写作质量反馈与 skill 机制故障
  - 没有独立的错误日志边界
- GREEN pass criteria:
  - 邮件反馈进入改进卡片
  - 机制异常进入 `errors.md`
  - `errors.md` 记录使用固定字段格式

## Baseline Observation

- Scenario 1:
  - Failure mode covered: 只修当前文档，不沉淀独立改进卡片
  - Observed behavior: 会修正文稿，也会口头说要沉淀长期规则，但没有落到单条反馈卡片文件，而是倾向直接改共享 skill 或写到 `errors.md`
  - Evidence excerpt: "我会把反馈沉淀成长期规则……优先记到共享层（原 writing-orchestrator，现 writing:write）；如果这次被视为一次典型踩坑或回归，再补记到 `errors.md`。"
  - Why this fails the spec: spec 要求用户反馈进入 `improvements/*.md` 的独立卡片，而不是直接写 skill 正文或混入 `errors.md`
  - Covered: yes
- Scenario 2:
  - Failure mode covered: 忽略文件化历史记忆检索，直接按口头规则开写
  - Observed behavior: 会口头提炼规则，也会说生成 checklist，但没有体现"先读取 `improvements/` 中 `status=active` 的匹配卡片"，也没有写后固定格式自检
  - Evidence excerpt: "会先显式提炼这条规则……会生成一个很轻量的 checklist。"
  - Why this fails the spec: spec 要求写前基于卡片文件检索生成 checklist，并在写后执行固定 schema 的 AI 自检；这里只是临时记忆中的规则复述
  - Covered: yes
- Scenario 3:
  - Failure mode covered: 虽然概念上分流，但没有按指定落盘边界执行
  - Observed behavior: 会把写作反馈与流程异常分开分类，但只给出抽象分类方案，没有落到 `improvements/*.md` 与 `errors.md` 的固定路径和模板
  - Evidence excerpt: "最好至少分成两类：`写作反馈库` 与 `流程异常/问题单`；两者可互相引用，但不要共用同一条记录。"
  - Why this fails the spec: spec 要求邮件反馈进入改进卡片、机制异常进入 `errors.md` 且使用最小字段格式；这里只停在概念分流，没有满足具体契约
  - Covered: yes

## Wrapper Static Check

（历史记录，基于 vault 中 writing-orchestrator 原 wrapper 检查结果；现已迁入 writing plugin）

- wrapper 文件路径: `.claude/skills/writing-orchestrator/SKILL.md`（原 vault wrapper）
  - 是否只跳转共享层: pass
  - 是否未复制 workflow: pass
  - 是否保留最小平台元数据: pass
- wrapper 文件路径: 原 vault 共享层（writing-orchestrator，现已迁入 writing:write）
  - 是否只跳转共享层: pass
  - 是否未复制 workflow: pass
  - 是否保留最小平台元数据: pass
- wrapper 文件路径: `.opencode/agents/writing-orchestrator.md`（原 vault wrapper）
  - 是否只跳转共享层: pass
  - 是否未复制 workflow: pass
  - 是否保留最小平台元数据: pass
- wrapper 文件路径: `.opencode/command/writing-orchestrator.md`（原 vault wrapper）
  - 是否只跳转共享层: pass
  - 是否未复制 workflow: pass
  - 是否保留最小平台元数据: pass

## GREEN Verification

- Scenario: 1 - 收到 spec 反馈后沉淀独立改进卡片
  - Expected behavior: 先识别为 `spec` 文档问题；写后输出固定格式 AI 自检；把反馈沉淀为 `improvements/` 下单独卡片，而不是写到 `errors.md`
  - Observed behavior: 验证时 agent 先检查 `errors.md`，再识别 `spec` 类型并生成 checklist；给出了固定格式 AI 自检；并把反馈沉淀为当前项目 writing_dir 下的 improvements/2026-03-19-spec-lead-with-conclusion.md
  - Pass/Fail: pass
- Scenario: 2 - 写前读取 `meeting-notes` active 卡片
  - Expected behavior: 先提炼 active 卡片规则并生成写前 checklist；正文结构先结论后讨论；写后执行固定格式自检
  - Observed behavior: agent 输出了包含结论摘要、决策事项、Action Items 的 checklist；meeting notes 开头结构符合规则；写后给出了固定格式 AI 自检结果，并说明没有新增卡片
  - Pass/Fail: pass
- Scenario: 3 - 改进反馈与机制错误分流
  - Expected behavior: 邮件反馈进入独立改进卡片；机制错误进入 `errors.md`；`errors.md` 使用固定字段模板
  - Observed behavior: 验证时邮件反馈被沉淀到当前项目 writing_dir 下的 improvements/2026-03-19-email-frontload-main-request.md；真实机制错误"worktree 中准备实现但编辑落在主工作区"被记录到本 skill 的 `errors.md`，且使用了固定字段模板
  - Pass/Fail: pass

# References

- source_type: llm, credibility: medium, 基于 `智元工作/工作记录/2026/03/19/writing-orchestrator/spec.md` 拆解的 RED/GREEN 压力场景
