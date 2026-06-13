---
name: write
description: 统一文档写作入口。当用户想要写 spec、方案、会议纪要、日报、周报、汇报文档、README、邮件或知识库说明文档时使用。
---

# 统一文档写作入口

当用户想写 `spec`、方案、会议纪要、日报、周报、汇报文档、README、工作邮件或知识库说明文档时，优先进入这个 skill。它是文档编写任务的默认前置 gate，用来在写前读取历史改进记忆、在写后执行固定格式 AI 自检，并把用户反馈或自检发现沉淀为可复用的单卡片改进记录。

## 目标

- 统一文档写作入口，而不是让不同文档类型各自散落地写
- 在写前读取当前项目 writing_dir 下的 improvements/ 中相关改进卡片（writing_dir 由 using-writing 注入，见 session 开头）
- 在写中根据目标、读者、结构组织文档
- 在写后执行固定格式 AI 自检
- 将用户反馈与 AI 自检发现沉淀为单卡片文件
- 将写作质量改进与 skill 机制故障分流

## 能力边界

这个 skill 适用于：

- `spec`、方案、会议纪要、日报、周报、汇报文档、README、工作邮件、知识库说明文档
- 起草、改写、润色、补全、重组、结构化文档内容
- 用户明确希望把本次反馈沉淀为长期写作经验

这个 skill 不适用于：

- 纯代码实现、调试、重构、测试
- 单纯解释概念但不产生文档交付物的问答
- 只做机械格式转换、无需写作判断的任务

## 集成契约

### 默认前置 gate

- 只要识别到任务属于文档编写，先进入 `writing:write`
- 先完成写前记忆读取、规则合并和 checklist 生成，再进入正文编写
- 若当前文档类型存在现有专用 skill 或目标目录规范，则把这些规则与 memory checklist 合并
- 真正落文档内容时，仍由本 skill 统一执行写中约束与写后自检

### 责任边界

- `writing:write`：任务识别、记忆检索、写作 checklist、自检、改进沉淀、错误分流
- 现有专用 skill：特定文档类型的领域结构、目录知识、模板偏好
- `errors.md`：skill 机制故障，不记录文档质量反馈
- 当前项目 writing_dir 下的 improvements/*.md：文档质量反馈、自检发现和长期改进规则

## 写前 workflow

### 1. 检查机制错误记忆

- 若本 skill 的 `errors.md` 存在，先阅读相关历史错误
- 若文件当前只有占位模板、没有真实错误记录，则跳过，不把模板正文误当成错误记忆
- 避免重复踩到已知的路由、模板、字段或分流问题

### 2. 识别文档任务

- 判断当前任务是否属于文档写作
- 识别文档类型，如 `spec`、`meeting-notes`、`email`、`readme`、`note`、`generic`
- 识别目标目录规范，如 `智元工作/`、`Zettelkasten/` 等

### 3. 检索改进卡片

只读取当前项目 writing_dir 下的 improvements/ 中 `状态: active` 的卡片，按以下顺序匹配：

1. 当前 `文档类型` 精确匹配
2. 若目标目录明确，再补充读取与目标目录相关的卡片
3. 若精确匹配为空，回退到 `文档类型: generic`
4. 若仍为空，则以空记忆集继续执行，不视为错误

写前记忆检索时忽略 `状态: superseded` 的卡片。

### 4. 合并规则并生成 checklist

- 把改进卡片规则、目录规范、现有专用 skill 约束合并为本次写作 checklist
- 冲突时按以下优先级：
  1. 当前文档类型专属卡片 > `generic`
  2. `来源: user` > `来源: ai-self-review`
  3. 更新更晚的卡片 > 更旧卡片

## 写中 workflow

- 先明确文档目标、目标读者、交付结构，再开始正文
- **BLUF 结构**：遵守 `writing:bluf` 的 L0-L3 四层信息架构，先定 L0（一句话结论），再列 L1（3-5 bullet），再展开 L2 正文
- **读者主线优先**：证据密集型文档必须先写"读者下一步该如何判断/行动"的主线，再把 source table、session id、search trace、blocked log、自检结果等研究过程信息下沉到附录或生成记录；正文不能按调查顺序或证据收集顺序展开
- 按文档类型组织骨架，而不是直接输出一大段泛化文本
- **per-kind template（写的脸）**：识别具体 kind 后，若当前项目 writing_dir 下的 `template/<kind>.md` 存在，**先 emit 它的 `## Layout` 字面骨架，再按 `## 写作 guide` 逐节填充，并删除内嵌「怎么填」微提示**——产出结构由 template 保证，不靠每次临场发挥。随后读 `patterns/<type>.md` 的「适用判定/反模式」做校准。**无 template 命中** → 回退读 `patterns/<type>.md` 内残留的写法骨架（无 pattern 则按 bluf L0–L3 通用结构起草），并 offer：用 `/readability-check distill <type> <语料...>` 起一份 template。template 文件规格见 `writing:readability-check` 的 `references/template-spec.md`。该写的脸的评判镜像（审的脸）在同 type 的 pattern，由 `writing:readability-check` 消费；结构/写法反馈经 `/readability-check evolve` 分诊固化回 template，写审同步收敛。已固化：`atomic-note`/`tech-spec`/`work-brief`（pattern 层）
- 保持事实、待确认信息、AI 推断边界清晰
- 避免空话、套话、重复表达和无信息密度段落
- 若目录规范或专用 skill 对结构有额外要求，正文必须遵守

## 写后 workflow

### 1. 执行固定格式 AI 自检

自检必须至少使用以下 schema：

```markdown
## 自检结果

- 目标对齐: pass | fail
- 读者适配: pass | fail
- 结构完整性: pass | fail
- BLUF 合规（L0 存在且为 assertion、信息分层合理、无 banned phrases）: pass | fail
- 读者主线清晰（正文先回答读者如何判断/行动，证据和生成过程未打断主线）: pass | fail
- 表达密度: pass | fail
- 历史规则符合度: pass | fail

## 问题清单

- 严重级别: high | medium | low
  - 问题: <具体问题>
  - 处理: <已修复 | 不修复及原因>

## 是否新增改进卡片

- yes | no
- 原因: <为什么新增或不新增>
```

### 2. 判断是否新增改进卡片

- `high` 问题且具备可复用性：必须新增卡片
- 同类 `medium` 问题在一次自检中出现 2 次及以上：新增卡片
- 单个 `low` 问题默认不新增，除非用户明确指出这是长期问题

### 3. 处理用户反馈

- 用户指出文档问题后，先完成本次修订
- 再把反馈抽象为长期可复用改进原则
- 以单独卡片写入当前项目 writing_dir 下的 improvements/

## 改进卡片规则

### 存放位置

- 改进卡片目录：当前项目 writing_dir 下的 improvements/
- 文件命名：`YYYY-MM-DD-<doc-type>-<short-slug>.md`

### 单卡片必备内容

- `创建日期`
- `来源`
- `文档类型`
- `状态`
- `适用范围`
- `关联文档`
- `tags`
- `## 触发场景`
- `## 原始反馈`
- `## 抽象后的改进原则`
- `## 可执行写法 / Checklist`
- `## 备注`

### `superseded` 规则

- 当一张新卡片明确替代旧卡片，旧卡片状态改为 `superseded`
- 新卡片在 `## 备注` 中显式引用被替代卡片
- 写前检索时忽略 `superseded` 卡片

## `errors.md` 边界

以下内容写入本 skill 的 `errors.md`：

- 文档类型判断错误
- 路由错误
- 模板或路径规范过时
- wrapper 未正确指向共享层
- 规则冲突导致执行异常

以下内容不要写入 `errors.md`：

- 文档太长、太空泛、结构不清楚
- 用户对表达方式的反馈
- AI 自检发现的内容组织问题

## `errors.md` 最小格式

```markdown
## YYYY-MM-DD HH:MM - <错误简述>

**触发任务**：<任务或文档类型>
**症状**：<观察到的异常>
**影响**：<对写作流程的影响>
**根因分析**：<已知原因或待确认原因>
**处理状态**：open | mitigated | resolved
**后续动作**：<是否需要更新 skill 或 wrapper>

---
```

## 完成检查

- 是否先读取了相关 `active` 卡片
- 是否合并了目录规范或专用 skill 规则
- 是否在写后输出了固定格式自检
- 是否把用户反馈或高价值自检发现写成单独卡片
- 是否把机制故障与写作改进正确分流

# References

- source_type: human, credibility: high, 用户在本次会话中确认的约束：全覆盖、自动优先、反馈卡片单文件、用户反馈 + AI 自检
- source_type: llm, credibility: medium, 基于 `智元工作/工作记录/2026/03/19/writing-orchestrator/spec.md` 的 workflow 与边界整理
