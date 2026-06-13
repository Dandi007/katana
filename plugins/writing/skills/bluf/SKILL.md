---
name: bluf
description: 用 BLUF（结论先行）+ 渐进式披露重组文档结构，解决 AI 生成文档"细节太多、信息倾泻"的问题。当用户想要重写文档使其更清晰、审查文档结构、或在写作过程中确保信息分层时使用。
user-invocable: true
argument-hint: "[file_path or topic]"
---

# BLUF — 结论先行 + 渐进式披露

解决一个核心问题：**读者不应该被迫从头读到尾才能知道你要说什么。**

## 能力边界

**覆盖**：所有工作文档（spec、email、状态报告、会议纪要、技术文档、README、One Page）

**不覆盖**：纯创意写作（小说、散文）、代码注释、聊天对话中的直接回答

## 核心模型：L0-L3 四层信息架构

| 层级 | 名称 | 内容 | 读者预期 |
|------|------|------|----------|
| **L0** | BLUF | 一句话结论/核心诉求 | 只读这句就能决策 |
| **L1** | Summary | 3-5 条 bullet 关键点 | 30 秒获得完整画面 |
| **L2** | Body | 完整论述、流程、证据 | 需要理解 why/how |
| **L3** | Appendix | 原始数据、边界条件、例外 | 需要复现或审计 |

### 层内 BLUF

每层、每个 section 的**第一句话必须是 assertion**（断言），不是 setup（铺垫）。

> Bad: "随着微服务架构的普及，服务间通信变得越来越复杂……"
> Good: "我们需要把 gRPC gateway 从 Envoy 迁移到 Connect，因为 Envoy sidecar 占用了 30% 的 pod 内存。"

### 信息归属三测试

在决定某条信息放哪一层时，依次问：

1. **频率测试**：80% 的读者需要它吗？→ Yes: L0-L1 / No: L2-L3
2. **决策测试**：它能触发行动吗？→ Yes: 上移 / No: 下沉
3. **依赖测试**：理解 A 需要先读它吗？→ No: 可以下沉

### 文档类型 L0 适配

不同文档类型的 L0 回答不同的问题：

| 文档类型 | L0 应该回答 |
|----------|------------|
| spec | 改什么、为什么改、边界是什么 |
| email | 核心诉求 + 期望动作 + 截止时间 |
| 状态报告 | 结论状态（正常 / 有风险 / 阻塞）+ 需要的帮助 |
| 会议纪要 | 关键决定 + action items |
| 技术文档 | 这个东西是什么、解决什么问题 |
| One Page | 一句话定位 + 核心价值主张 |

## AI 反模式检测

写完文档后，做一轮 audit pass 检查以下问题。

### Tier 1 — 直接禁止的表达

发现即删，无需讨论：

| 类别 | 示例 |
|------|------|
| Hedging 对冲语 | `值得注意的是` / `需要考虑的是` / `It's worth noting` / `It's important to` |
| 空洞连接词连续出现 | `Moreover` / `Furthermore` / `In addition` / `此外` 连续两个以上 |
| 意义膨胀词 | `robust` / `pivotal` / `groundbreaking` / `game-changing` → 换成具体描述 |
| Meta-commentary | `让我来解释` / `接下来我们看看` / `首先我们需要考虑` → 直接给内容 |
| 空洞开场 | `随着……的发展` / `在当今……背景下` / `众所周知` |
| 过度礼貌 | `That's a great question!` / `Absolutely!` → 直接回答 |

### Tier 2 — 结构性反模式

发现即重组：

- **Throat-clearing**：用一大段背景"热身"才进正题 → 把结论从末尾移到开头
- **Evidence-first**：按思考顺序组织（先证据后结论）→ 翻转为阅读顺序（先结论后证据）
- **Buried ask**：请求/决策藏在段落中后部 → 提到第一句
- **Info dumping**：所有细节平铺同一层级 → 按 L0-L3 分层
- **Over-sectioning**：短文档过度分段（300 字分 5 个 H2）→ 合并到自然段落
- **Premature detail**：读者还没建立 mental model 就给实现细节 → detail 下沉到 L2-L3

### Tier 3 — 写后压缩

- 目标：初稿砍 30-40% token
- 合并重复 bullet，3 句话压 1 句
- 禁止连续 3 个相同长度/结构的段落（打破节奏单调）
- 删除不影响理解的修饰语和限定词

## Workflow

### 模式一：独立调用（`/bluf [file_path]`）

1. **读取目标文档**
2. **分层诊断**：当前文档的信息在哪一层？有没有 L0？L0 是 assertion 还是 setup？
3. **标记违规**：逐条检查 Tier 1/2/3 反模式
4. **重组**：按 L0-L3 重写，每层内部 BLUF
5. **Audit pass**：检查 Tier 1 banned phrases 是否清除、压缩率是否达标
6. **输出**：重写后的文档 + 一段简短的改动摘要（改了什么、为什么）

### 模式二：写中引用（被 writing:write 调用）

writing:write 在写中阶段应遵守以下规则：

1. **先定 L0**：在写任何正文前，先写出一句话 BLUF
2. **骨架先行**：先列出 L1 的 3-5 个 bullet，再展开 L2
3. **层内 BLUF**：每个 section 第一句是 assertion
4. **下沉检查**：写完每段问自己——这段 80% 的读者需要吗？不需要就下沉
5. **写后 audit**：完成后跑一遍 Tier 1/2/3 检查

### 模式三：对正在写的内容实时检查

用户在写作过程中调用 `/bluf`，对当前草稿做快速诊断：

1. 指出 L0 缺失或不够 assertion
2. 标记 info dumping 区域
3. 建议哪些内容应下沉
4. 不做完整重写，只给诊断 + 建议

## 集成说明

### 与 writing:write 的关系

- bluf 是 writing:write 写中阶段的**规则提供者**
- writing:write 的写后自检增加 BLUF 合规检查：
  - `L0 存在且为 assertion`: pass | fail
  - `信息分层合理`: pass | fail
  - `无 Tier 1 banned phrases`: pass | fail

### 与 writing:readability-check 的关系（写审共享）

- bluf 是**结构/分层的共享 SSoT**，写、审两侧都引用，不被任何一侧吸收或退役。
- `writing:write`（写）按本 skill 的 L0–L3 + 文档类型 L0 适配搭骨架；`writing:readability-check`（审）按本 skill 做结构维度的机检（L0 是否 assertion、分层是否合理、Tier1 banned phrases）+ 冷读判断。
- 各 per-type pattern 的「结构反模式」一律以本 skill 的 L0 适配 + Tier1/2/3 为准，不在 pattern 里复制。

### Superseded 改进卡片

本 skill 创建后，以下 writing:write 改进卡片标记为 `superseded`：

- `2026-03-19-spec-lead-with-conclusion.md` — 被 L0 spec 适配规则覆盖
- `2026-03-19-email-frontload-main-request.md` — 被 L0 email 适配规则覆盖

## errors.md 边界

以下写入 `errors.md`（本 skill 同目录）：

- 分层诊断逻辑错误
- 与 writing:write 集成异常
- Tier 1 banned phrases 漏检

以下不写入 errors.md：

- 用户对具体文档措辞的偏好（那是 writing:write 的改进卡片）

# References

- US Army AR 25-50 (Preparing and Managing Correspondence) — BLUF 的军事起源
- Nielsen Norman Group: Progressive Disclosure — 渐进式披露的 UX 起源
- https://en.wikipedia.org/wiki/BLUF_(communication)
