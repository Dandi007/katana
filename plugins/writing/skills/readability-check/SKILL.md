---
name: readability-check
description: 文档可读性自检——按文档类型固化 pattern，用「机检 + 冷读 subagent」混合引擎做行号级可读性 review，并把你的反馈沉淀回共享 pattern 自我进化。当用户想检查一份文档好不好读、能不能独立看懂、是否符合该类型的写作规范，或想把 review 意见沉淀成长期规则时使用。不用于事实核对（verify）、论证质疑（critical-review）、去 AI 味（humanizer-zh）。
user-invocable: true
argument-hint: "[file_path ...] | evolve [type] | distill <type> <语料...>"
---

# Readability Check — 文档可读性自检

解决两件现有 review skill 分开做、且都没记忆的事：

1. **从固化 pattern 入手检查，而不是每次现编标准。** 把几类高频文档「好的样子」固化成 per-type pattern，检查就变成「对照 pattern」这件确定的事——而固化 pattern 反过来让写作也收敛。
2. **从你的反馈自我进化。** 你 review 时提的意见沉淀回**共享 pattern 池**，让写、审两侧一起越来越懂「你心里的可读」。

## 与「写」是同一份知识的两张脸

可读性 pattern 是**一份 per-type SSoT，长两张脸**：

| | 写的脸（generative） | 审的脸（evaluative，本 skill） | 共享层 |
|---|---|---|---|
| 内容 | 模板骨架 + 写法 | 硬规则(机检) + 软规则(冷读) + checklist | 适用判定 / 反模式 / **演进知识** |
| 消费方 | `writing:write`（写前读、写后自检） | `writing:readability-check`（机检 + 冷读两趟） | 两者都读 |

- per-type pattern：当前项目 writing_dir 下的 patterns/<type>.md（writing_dir 由 using-writing 注入，见 session 开头）（共享 SSoT，两张脸同居一文件）
- per-type 演进卡池：当前项目 writing_dir 下的 improvements/*.md（反馈历史，`evolve` 把它提炼进 pattern）
- 结构/分层规则：`writing:bluf`（L0–L3 + AI 反模式，写审共享）

**收敛环**：本 skill review 提的意见 → 写进同一份 pattern/卡池 → 下次「写」时 writing:write 读到 + 下次「审」时本 skill 查到。

## 能力边界

**覆盖**：判断一份（或一组）文档好不好读、能不能脱离上下文独立看懂、是否符合该类型写作规范；把 review 意见固化成长期规则。

**不覆盖**（在报告里指路，不自己做）：

| 想做 | 改用 |
|---|---|
| 核对事实是否与代码/官方一致 | `verify` |
| 质疑论证 / 挑推理漏洞 | `critical-review` |
| 去 AI 味、润色语言 | `humanizer-zh` / `humanizer` |
| 自动 writer⇄reviewer 循环修到通过 | `review-loop` |
| 原子笔记审计（去重/勘误/相关集合） | `audit-note` |
| 从零开始写文档 | `writing:write` |

## 首批支持的类型（typed 快车道）

| type | 适用判定锚点 | pattern |
|---|---|---|
| `atomic-note` | wiki 根原子卡（`类型: 卡片`）、有 frontmatter + References + wikilink | 当前项目 writing_dir 下的 patterns/atomic-note.md |
| `tech-spec` | 技术设计/spec、SPEC-NNN、work folder `spec.md` | 当前项目 writing_dir 下的 patterns/tech-spec.md |
| `work-brief` | 工作汇报 / One Page / 周报 / 状态报告，`智元工作/` 下 | 当前项目 writing_dir 下的 patterns/work-brief.md |

> 上述为 pattern（审的脸）。对应的 template（写的脸 = Layout + 写作 guide）若已 distill 出，机检会校验产出与其 `## Layout` 的结构符合性；未 distill 的类型只跑 pattern checklist。

不命中任一类型 → 走 `references/_generic.md` 兜底（cold-read 自包含性 + bluf 结构），检完 offer：是否为这类文档起一份新 pattern。

## 路由

| 意图 | 走 |
|---|---|
| `/readability-check <file...>` 检查文档 | 「检查流程」 |
| `/readability-check evolve [type]` 固化反馈 | 「进化流程」 |
| `/readability-check distill <type> <语料...>` 从语料冷启动 template+pattern | 「Distill 流程」 |
| 检查中/后用户给可读性意见 | 即时采集进当前项目 writing_dir 下的 staging/inbox.md |

---

## Distill 流程（抽离 = 冷启动）

把一类文档的现有好语料冷启动成首套「写的脸 + 审的脸」。详细蒸馏指引见 `references/distill-prompt.md`。

1. 收集 type + N 篇该类型现有好文档（语料）。
2. 按 `references/distill-prompt.md` 蒸馏：共性结构 → 当前项目 writing_dir 下的 `template/<kind>.md` 的 `## Layout`；写法 → `## 写作 guide`；评判维度 → `patterns/<type>.md`。
3. 外部已有 schema 的类型，Layout 对齐 + link，不重定义。
4. **人工 gate**：首稿先呈 diff，经用户**确认**后才落盘（防 model-collapse）。
5. 落盘后立即对写（template）、审（pattern）两侧生效。

> distill 是冷启动；日常持续优化走「进化流程」的 evolve 分诊。

---

## 检查流程（混合引擎）

### Step 0 — 类型识别

1. 读被检文档；逐个 pattern 跑「适用判定」+ 文件路径/frontmatter 特征匹配。
2. 命中 → 该 type；多命中取最具体；不命中 → `_generic`。
3. **定 kind（供结构机检）**：一个 type 可对应多个 `template/<kind>.md`。按被检文档的路径/frontmatter/标题特征确定具体 kind，结构符合性机检对照该 kind 的 `## Layout`；定不了具体 kind 或该 type 无 template → 跳过结构机检，只跑 pattern checklist，不硬套错骨架。
4. 载入该 type 的尺子：
   - 当前项目 writing_dir 下的 patterns/<type>.md（审的脸 checklist + 反模式）
   - 当前项目 writing_dir 下的 improvements/*.md 中 `状态: active` 且 `文档类型` 匹配的卡（演进规则）
   - `writing:bluf` 的该类型 L0 适配 + Tier1/2/3 反模式
   - `references/self-containment-checklist.md`（自包含性机检项）

### Step 1 — 机检 pass（主 agent，便宜）

对 checklist 中标 `[机检]` 的条目逐条机械检测，能 grep 的用 grep（见 `references/self-containment-checklist.md` 的命令）：

- 未定义术语 / 未解释内部编号（F17、§08、bg_*、ses_*、MR !*）
- 未落地跨引用、历史引用（"用户原话"、"X 日讲过"）、内网 URL/IP
- 该 type 必含/必缺 section、frontmatter 字段、References 是否齐全
- BLUF：L0 是否存在且为 assertion（首句是断言不是铺垫）
- bluf Tier1 banned phrases（hedging / 空洞连接词 / meta-commentary）
- **结构符合性**：产出是否符合当前项目 writing_dir 下的 `template/<kind>.md` 的 `## Layout`（必含节 / frontmatter key / 顺序）；缺节/缺字段/乱序逐条报行号。**无对应 template 则跳过此项**，不报缺失。

产出：行号级问题（规则来源 + 行号 + 现状 + 建议）。

### Step 2 — 冷读 pass（prompt 强约束 subagent，保真）

**必须派 subagent，主 agent 自己带上下文不可信。** 用 `Agent` 工具（`subagent_type: "Explore"` 或默认 general-purpose），prompt 用 `references/cold-read-prompts/single-doc-prompt.md`。

> 注：约束是 **prompt 层的强约束**（在 prompt 里明令只读被检文档、并**列出具体禁读文件名**），不是工具级白名单——Agent 工具不强制文件白名单，所以禁读清单必须写全、写具体，否则 subagent 可能自行打开"看起来相关"的文件。

强约束（写进 prompt）：

1. 只能读被检文档**这一个文件**，禁读同 work folder / repo 的任何其他文件（列出具体禁读文件名）
2. 不知道作者与用户的历史对话、checkpoint、session summary
3. 角色要具体（"第一次接手这份 spec 的前端 tech lead" / "新入职工程师" / "未参会同事"），按 type 选（见 single-doc-prompt 的角色变体表）
4. 不客气、不鼓励、不先肯定后否定——直接找问题
5. 每条问题精确到行号或段落；自包含性好就直说，不凑数

**多份相关文档**：每份 1 个独立冷读 + 1 个交叉一致性（用 `references/cold-read-prompts/cross-consistency-prompt.md`），**全部在同一响应里 `run_in_background: true` 并行派出**。

### Step 3 — 合并报告

机检项 + 冷读项合一，按严重度排序：

```markdown
## 可读性 review：<doc>（type=<type>）

| 维度 | 结论 |
|---|---|
| 类型识别 | <type / generic + 依据> |
| 机检 | <N 项> |
| 冷读（<角色>） | <一句话结论> |

### 🔴 P0（不修会直接误读）
| # | 问题 | 位置 | 命中规则 | 改法 |
|---|---|---|---|---|
### 🟠 P1（严重影响可读/可实现）
### 🟡 P2（可延后）

### 跨界（非本 skill 范畴，指路）
- 事实存疑 → /verify；论证薄弱 → /critical-review；AI 味 → /humanizer-zh
```

每条标来源（`[机检]` / `[冷读]`）。用 `AskUserQuestion` 让用户选后续：全修 / 只修 P0 / 挑几条 / 发原始 review 自己修。

### Step 4 — 收尾

- 用户当场给意见 → 进「进化流程 A」。
- 走的是 `_generic` → offer：这类文档是否值得起一份新 pattern（当前项目 writing_dir 下的 patterns/<新type>.md）。

---

## 进化流程（两层：raw → compiled，落共享池）

### A. 即时采集（不立即改 pattern）

用户给一条可读性意见 → append 到当前项目 writing_dir 下的 staging/inbox.md，保留原话：

```markdown
## [YYYY-MM-DD HH:MM] type=<type|generic> doc=<路径>
- <用户原话意见>
- status: pending
```

raw 层 immutable，当场不动 pattern。

### B. 批量固化（`/readability-check evolve [type]`，人工 gate）

1. 读当前项目 writing_dir 下的 staging/inbox.md 中 `status: pending` 条目（可按 type 过滤）。
2. 把零散意见提炼成**具体可执行**的 rule：明确 `[机检]` 还是 `[冷读]`、适用范围、例外。
3. **分诊后 promote**（永远经用户确认才写 → 防 model-collapse）。先判反馈性质，再落对应文件：

   | 反馈性质 | 落到 |
   |---|---|
   | 结构（缺节 / 缺 frontmatter key / 顺序错） | 当前项目 writing_dir 下的 `template/<kind>.md` 的 `## Layout` |
   | 写法（这节怎么填得更好） | 当前项目 writing_dir 下的 `template/<kind>.md` 的 `## 写作 guide` |
   | 评判 / 反模式 / 适用判定 | 当前项目 writing_dir 下的 `patterns/<type>.md` 的 checklist/反模式 + 在 improvements/ 新增一张 `来源: review` 演进卡（沿用 `writing:write` 的 `templates/improvement-card.md` 格式） |

   **写法反馈进 template 的 `## 写作 guide`，不进 patterns**——pattern 只收「怎么判」（评判 / 反模式 / 适用判定）；写的脸（结构 + 写法）已整体迁出到 template，不要再把「怎么写」塞回 pattern。

   - 该 type 尚无 template → 结构/写法反馈触发 distill（见「Distill 流程」）起首稿，而非塞进 pattern。
   - 该 type 尚无 pattern → 新建 `patterns/<新type>.md`（结构遵 `references/pattern-spec.md`）+ 首张演进卡。
4. 用户确认 → 写入；对应 inbox 条目改 `status: compiled`。
5. 因落共享池：该 rule 立刻对**写、审两侧同时生效**。

---

## 硬规则

1. **冷读必须用 subagent**，主 agent 不能自己冷读（带上下文不可信）。
2. **subagent 的禁读约束是 prompt 层强约束**（非工具白名单）：必须在 prompt 里**列全具体禁读文件名**，且角色具体。
3. 输出**精确到行号/章节**，不给"建议改清楚些"这种空话。
4. 进化**永远人工 gate**：raw immutable，compiled 经确认。
5. **只管可读性**，事实/论证/AI 味跨界问题指路到对应 skill，不越界。
6. 固化产物**归一到共享池**（writing_dir 下 `template/`=结构/写法、`patterns/`+`improvements/`=评判），不在本 skill 自建平行库。

## 常见坑

见 `errors.md`（执行前先看，遇错 append）。高频：忘给 subagent 硬约束、主 agent 代替 subagent、多文档只派一个 subagent、固化时直接写不经确认、把跨界问题揽进来。

# References
- `references/cold-read.md` — 冷读 subagent 机制（吸收自已退役的 cold-read-review，适配本环境 Agent/AskUserQuestion）
- `references/self-containment-checklist.md` — 自包含性机检清单（含 grep 命令）
- `references/cold-read-prompts/single-doc-prompt.md` — 单文档冷读 subagent prompt 模板
- `references/cold-read-prompts/cross-consistency-prompt.md` — 多文档交叉一致性 prompt 模板
- `references/_generic.md` — 无类型兜底维度
- `references/template-spec.md` — 写的脸（`template/<kind>.md`）文件规格
- `references/pattern-spec.md` — 审的脸（`patterns/<type>.md`）文件规格（固定骨架 + 写的脸节恒为指针）
- 当前项目 writing_dir 下的 patterns/ — 共享 per-type pattern（写审两张脸 SSoT）
- 当前项目 writing_dir 下的 improvements/ — per-type 演进卡池
- `writing:bluf` — L0–L3 结构 + AI 反模式（写审共享）
