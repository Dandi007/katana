---
name: first-principles-thinking
description: 从第一性原理重建判断，识别真实约束与未验证假设；不用于事故因果链排查或结构性反馈回路分析；需落盘留档的完整分析走 fpa。
---

# First Principles Thinking

## Overview

这是一个基础思维方式 guide，用于在做判断前把问题拆回不可再分的事实、真实约束和未验证假设。核心原则：不要从行业惯例、相似案例或现成方案出发；先问"这件事实际由什么构成，哪些约束真的不能改？"

关键词：first principles、第一性原理、fundamental truths、assumption、constraint、hard constraint、soft constraint、reasoning by analogy、重建判断、基础思维。

## Core Pattern

| 步骤 | 问题 | 产物 |
|---|---|---|
| Deconstruct | 这件事实际由什么构成？ | constituent parts、真实数据、成本/收益拆分 |
| Challenge | 哪些是不可变现实，哪些只是选择或假设？ | hard / soft / assumption 分类表 |
| Reconstruct | 如果只保留真实约束，最简单有效的方案是什么？ | 从 fundamentals 得出的判断或新方案 |
| Validate | 哪个关键假设最需要被验证？ | 最小实验、度量口径、决策门槛 |

### Constraint Classification

| 类型 | 判定标准 | 例子 | 处理方式 |
|---|---|---|---|
| Hard constraint | 物理、数学、法规、明确资源上限、外部合同等短期不可变现实 | 光速限制、预算上限、已确认不可协商的合同日期 | 接受并围绕它优化 |
| Soft constraint | 团队、流程、政策、技术选型等可被改变的选择 | "我们一直用 REST" | 问谁决定的、为什么、改动代价是什么 |
| Unvalidated assumption | 没有证据支撑的判断或恐惧 | "用户不会接受""只能换供应商" | 设计验证实验或直接标记为未证实 |

## Quick Reference

| 默认反应 | First Principles 反应 |
|---|---|
| 找类似案例 | 先拆基本构成 |
| 接受行业价格 | 拆单价、用量、浪费、有效产出 |
| 把工具当目标 | 追问真正要完成的 function |
| 把政策当现实 | 区分 hard constraint 与 soft constraint |
| 在旧方案上打补丁 | 从真实约束重新构造最小方案 |

## Output Template

本 skill 只保留 Lite（inline）输出——校准层职责，不落盘。需要留档的完整分析走 `/fpa:fpa` 强约束入口（见下方路由）。

### Lite 模板（inline）

```markdown
| Constraint / Claim | Type | Evidence | Challenge |
|---|---|---|---|

**Reconstruction**: [只保留真约束后，重建出的最简判断或方案]
**Key Insight**: [一句话说明哪个默认假设限制了判断]
```

Lite 也必须包含 Reconstruction 行——只输出约束表和 insight 等于"只 Challenge 不 Reconstruct"，是本 skill 自己定义的失败模式。

### 需要留档 → 走 /fpa:fpa

Type 1 / one-way door 决策、用户明确要求留档、分析需要跨 session 复用时，**不在本 skill 内落盘**——改走强约束入口（同 plugin 的 `../fpa/SKILL.md`）：永远 Full 四步 + Workflow skeptic 对抗验证 + 机械验收，落盘 `FPA-<topic-slug>.md`。

分工：本 skill 管对话内思维校准（Lite），fpa 管留档产物（Full + 对抗验证）。两者并存但落盘只有一条路，避免双轨漂移。

## Example

**输入**：我们应该把 3 人维护的内部工具拆成 microservices，因为现代系统都这么做，未来也方便扩展。

### Deconstruction

- 真实目标：让内部工具更容易维护、演进、扩展。
- 基本构成：3 人团队、内部用户、部署链路、模块边界、故障排查、未来负载不确定。
- 当前证据缺口：没有证明性能、独立发布、团队边界或隔离需求已经成为瓶颈。

### Constraint Classification

| Constraint / Claim | Type | Evidence | Challenge |
|---|---|---|---|
| 3 人团队维护能力有限 | Hard constraint | 人力短期固定 | 架构必须降低运维面 |
| "现代系统都用 microservices" | Unvalidated assumption | 只是行业类比 | 现代不等于适合当前系统 |
| 未来要扩展 | Unvalidated assumption | 未说明扩展维度 | 先定义是流量、代码、团队还是发布频率 |
| 可以先模块化 | Soft constraint | 工程选择 | 比拆服务更低风险 |

### Reconstruction

- Fundamental truths：小团队需要低运维复杂度、清晰边界、可测试、可逐步演进。
- Simplest effective solution：先做 modular monolith，补模块边界、测试、日志和部署自动化；只有出现稳定边界和独立扩容证据时，再拆单个服务。

### Validation / Measurement Plan

- Key assumption to test：当前系统已经存在只能靠 service split 解决的扩展压力。
- Smallest experiment or measurement：统计最近两个迭代的性能瓶颈、发布冲突、模块耦合改动、故障定位时间；同时先做一个模块边界清理试点。
- Decision threshold：如果 modular monolith 仍无法解决独立扩容、独立发布或强隔离需求，再拆第一个边界稳定的服务。

### Key Insight

限制判断的不是"架构是否现代"，而是把"未来扩展"当成了已验证约束。

## Red Flags

看到这些句式时，必须暂停并拆回 fundamentals：

- "大家都这么做。"
- "行业就是这样。"
- "没办法，只能……"
- "以后肯定需要。"
- "这不是技术问题，是流程规定。"
- "用户不会接受。"
- "这个方案更现代 / 更先进。"

## Rationalization Table

| Rationalization | Reality |
|---|---|
| "时间紧，先沿用惯例。" | 时间紧更需要避免把 soft constraint 当 hard constraint。 |
| "这个问题太明显，不用拆。" | 最明显的结论往往来自类比，而不是 facts。 |
| "只要最终建议对就行。" | 没有拆解过程，后续场景无法复用判断。 |
| "第一性原理太哲学。" | 它是约束分类和重建判断，不是空泛讨论。 |

## Common Mistakes

- **把组件清单当作第一性原理**：拆得更小不等于拆到 fundamental truths；必须追问真实成本、物理/资源限制、目标函数。
- **把所有约束都软化**：法律、预算、物理限制、已确认不可协商的外部合同可能是真约束，不能靠想象移除；内部目标日期通常先按 soft constraint 检查。
- **只 Challenge 不 Reconstruct**：质疑假设之后必须给出基于真实约束的新判断。
- **过度输出**：简单问题用 Lite 模板（constraint table + Reconstruction + Key Insight）即可，不要写成长篇哲学论文；留档需求走 /fpa:fpa，本 skill 不自带落盘模式。

## Baseline Test Notes

无本 skill 的自然回答通常能给出合理建议，但容易省略显式的 constraint classification 和 reconstruction，因此难以复用为稳定思维框架。本 skill 的目标是把"好直觉"固化成可重复检查的 guide。

# References

- personal_ai_infrastructure (PAI) FirstPrinciples pack：SKILL.md 与 Deconstruct / Challenge / Reconstruct workflows（方法雏形来源，credibility: medium）
- `../fpa/SKILL.md`（强约束执行入口，同 plugin）
