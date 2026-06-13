# Single-Doc Cold-Read Prompt Template

> 单文档冷读 review 的 subagent prompt 模板。
> 使用方法：把模板里 `<ANGLE_BRACKETS>` 占位符替换成实际内容，作为 `task(subagent_type="oracle", prompt="...", run_in_background=true, load_skills=[])` 的 prompt。

---

```text
ROLE: 你是一个**第一次打开这份文档**的读者。你是 <目标读者角色，比如"前端 tech lead"、"外部评审方"、"新入职工程师"、"产品经理">。你**没看过**这个 work folder / repo / 项目里的任何其他文件，你**没看过**之前任何 session 的 checkpoint，你**不知道**作者和用户之间的历史对话。

你唯一可以读的文件是：
`<目标文件的绝对路径>`

**硬约束**：你不能打开 <列出该 work folder / repo 里所有相关文件的绝对路径，特别是：findings.md / golden-order.md / spec.md / plan.md / context.md / progress.md / AGENTS.md / 以及相邻 work folder 下的相关文档>。如果文档引用了这些外部文件，你只能当它"看不到"处理。

GOAL: 站在 <目标读者角色> 的视角，判断这份文档的**自包含性**和**可读性**。

REVIEW 问题清单：

1. **能不能独立看懂核心内容？** 读完后，你能不能用自己的话说出：
   - 这份文档是为谁写的
   - 推荐做什么 / 需求是什么
   - 关键约束是什么
   - 有什么开放问题

2. **有没有依赖外部上下文的术语、编号、代号**？列出所有**在本文档内未解释**的：
   - 项目编号（比如 FI-xxx / Linear ID / Issue #）
   - 代号 / 分支名 / 文件路径（比如具体的分支名、仓库路径、镜像代号）
   - 人名（比如张三、liuyi）
   - 内部术语（比如内部缩写、团队内部代号）
   - 内部编号（比如 F17、§08、Q-XXX、bg_*、ses_*、MR !* 在本文档内是否有定义）
   - URL（特别是内网 IP / 只有特定成员能访问的链接）

3. **术语节 / 前置约定节够不够用**？如果文档有类似的节，评估每个条目：
   - 定义是不是 circular（用没定义的词解释新词）
   - 能不能从这个定义推出文档后文的含义
   - 有没有遗漏（后文出现但本节没定义的关键术语）

4. **跨引用的完整性**：文档里说"见同目录 xxx"、"承接 yyy 报告"的地方，**假设你不打开那个文件**，你还能理解当前段落的意思吗？

5. **可执行性**：如果你是被指派来基于这份文档 <实施 / 评审 / 追问 / 执行>，你能在不追问作者的前提下开始工作吗？哪些问题必须追问？

6. **对作者的具体修改建议**：列出 3-10 条**具体修改建议**（精确到"把第 X 行的 Y 改成 Z"，或"§2.3 缺少对 XXX 的解释，建议加一句 'XXX 是 ...'"）。

**输出格式**：Markdown。分 6 节对应上面 6 个问题。不要泛泛而谈，每条都要能落到具体行号或段落。**不要客气、不要鼓励、不要先肯定后否定——直接找问题**。如果文档自包含性很好，直接说"自包含性良好"并给出理由；如果有问题，按严重程度排序。
```

---

## 调用样例

```python
task(
    subagent_type="oracle",
    load_skills=[],
    run_in_background=True,
    description="Cold-read review: frontend-ui-needs.md",
    prompt="""
ROLE: 你是一个**第一次打开这份文档**的读者。你是**前端 tech lead / 产品经理**...

你唯一可以读的文件是：
`/path/to/frontend-ui-needs.md`

**硬约束**：你不能打开 `electron-design-analysis.md`、`findings.md`、`golden-order.md`、...

GOAL: ...

REVIEW 问题清单：
1. ...
"""
)
```

## 角色变体建议

针对不同文档类型，推荐的冷读角色：

| 文档类型 | 推荐冷读角色 |
|---|---|
| 产品需求 / 用户 story | 第一次接手的产品经理 / 前端 tech lead |
| 架构 / 技术方案 / ADR | 外部架构 reviewer / 新入职后端工程师 |
| API spec | 第一次对接的 client 开发者 |
| Runbook / 运维文档 | 值班 oncall 新人 |
| Onboarding 文档 | 入职第一天的新员工 |
| 会议纪要 / 决策备忘 | 未参会的团队外同事 |
| spec / 标准 | 实现者 + 评审方（两个角色要派两个 subagent）|
| 学习笔记 / 技术 blog | 该领域有基础但不熟悉具体项目的读者 |

## 针对性的额外 review 问题（按文档类型扩展）

除了 6 个核心问题，按文档类型可补充：

**API spec 类**：
- 每个 endpoint 都有 request/response schema 吗？
- 错误码枚举完整吗？
- 认证/幂等/限流规则清楚吗？

**需求类**：
- 能直接据此画 wireframe 吗？
- 字段都有 schema（type / 必填 / 默认 / 示例）吗？
- 状态转换 / 错误态 / 空态都定义了吗？

**架构类**：
- 部署拓扑图能独立看懂吗？
- 进程边界 / 通信协议 / 部署形态清楚吗？
- 风险和开放问题都列了 owner 和 blocking 关系吗？

**Runbook 类**：
- oncall 新人能按步骤走完吗？
- 每一步的"成功标志"明确吗？
- 失败恢复路径列了吗？

## 禁忌

- **不要**在 prompt 里告诉 subagent "这份文档已经经过多轮修改"——会让它产生 anchoring bias
- **不要**说"review 一下，但是别太严格"——等于放弃 review
- **不要**让 subagent 同时 review 两份文档——它会把它们当整体，失去冷读价值
- **不要**在 `硬约束` 里只写"不能打开相关文件"——要**列具体文件名**，否则 subagent 会自己判断"这个文件大概有关"然后打开
