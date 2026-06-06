---
name: fpa
description: 对一项工作/决策做强约束的第一性原理完整分析并落盘 FPA 文档——永远 Full 模式四步 + Workflow skeptic 对抗验证 + 机械验收。当用户说「用第一性原理审视/分析某项工作」「出一份 FPA」「第一性原理分析并留档」，或要判断某个方案/架构/投入是否被默认假设绑架（例：「我们真的需要拆 microservices 吗」「这个报价/做法凭什么是合理的」）时必须使用；对话内轻量思维校准走 first-principles-thinking。
---

# FPA — First Principles Analysis（强约束入口）

## Overview

本 skill 是 `first-principles-thinking` 的**强约束执行入口**：方法定义（四步循环、约束三分类、Red Flags）的 source of truth 在同 plugin 的 `../first-principles-thinking/SKILL.md`，本 skill 不复述方法，只强制执行流程并落盘产物。

与校准层的分工：

| | first-principles-thinking（校准层） | fpa（本 skill） |
|---|---|---|
| 触发 | ambient——对话中出现 red-flag 句式、轻量判断 | 显式——用户要求对某项工作做完整分析 |
| 输出 | Lite（inline）或 Full | **永远 Full，无 Lite** |
| 验证 | 自检 | Workflow skeptic 对抗验证（语义）+ validate 脚本（机械） |
| 落盘 | 按需 | **必须**，`FPA-<topic-slug>.md` |

机械验收随 plugin 安装即生效（`hooks/hooks.json` 注册 PostToolUse hook），无需消费 repo 任何配置。

## 输入

- **主题**（必需）：要分析的工作、决策或主张。
- **落盘路径**（可选）：用户指定则最优先。

> 触发提示：description 自然语言路由的命中是概率性的，「必须留档」的高确定性场景请显式敲 `/fpa:fpa <主题>`，不要依赖自动路由。

## 踩坑记录约定

plugin 目录只读。执行中遇到错误/踩坑，append 到**消费 repo** 的 `docs/fpa/errors.md`（无则创建）；执行前若该文件存在，先读以避免已知坑。项目 CLAUDE.md 可覆盖该路径。

## Workflow

### Phase 0 — 前置

1. 运行 `date "+%Y-%m-%d %H:%M"`。
2. 读取同 plugin 的 `../first-principles-thinking/SKILL.md`（方法定义）与消费 repo 的 `docs/fpa/errors.md`（如存在）。
3. 确定落盘去向：用户指定路径 > active work folder（与 `findings.md` 同级）> `docs/fpa/`（项目 CLAUDE.md 可声明覆盖默认）。

### Phase 1 — 四步草稿（单 agent，顺序执行）

按四步循环产出草稿：Deconstruct → Challenge（约束三分类表）→ Reconstruct → Validate。

硬规则：

- **禁止把四步拆给不同 subagent**——四步是顺序推理，必须共享同一上下文。
- Deconstruct 步必须包含**需求拆解**表：把源需求拆成子需求，每条标注「现有方案中由什么满足」——没有任何部分满足的子需求就是设计缺口，必须在 Reconstruct 步补位或显式列为非目标。（实证：本 skill 自指分析中，两个真实设计缺口正是分别由 skeptic 自下而上核对与用户人工各自从需求拆解的空格里发现的。）
- 证据缺口显式写入草稿（"当前证据缺口：…"），禁止编造数据。
- 分类表中每条 claim 必须有 Evidence 列；写不出 evidence 的 claim 自动归为 Unvalidated assumption。

### Phase 2 — Adversarial verify（Workflow）

两档验证，按决策风险选择：

| 档位 | 何时用 | 形态 |
|---|---|---|
| N+1 fan-out（**当前默认**） | Type 1 / one-way door、用户显式要求；A/B 实验完成前的所有运行 | 每条 unvalidated assumption 一个 skeptic + reconstruction 一个，并行 |
| 单 verification pass | A/B 实验通过后成为非 Type-1 的默认 | 一个 fresh-context agent 一次核查全部 assumptions + reconstruction，逐条取证 |

> 两档的共同本质：语义验证的有效成分是 **fresh context + 工具取证的外部反馈**（Kamoi, arXiv:2406.01297 的 reliable external feedback），不是「同模型第二意见」。fan-out 买的是单条取证深度与墙钟，不是更多「意见」。

调用 Workflow tool，script 模板（`mode` 切换两档；args 必须携带约束表 Evidence 列与草稿摘要）：

```js
export const meta = {
  name: 'fpa-skeptics',
  description: '对 FPA 草稿的 assumptions 与 reconstruction 做对抗验证',
  phases: [{ title: 'Refute' }],
}
const VERDICT = {
  type: 'object',
  properties: {
    target: { type: 'string' },
    verdict: { type: 'string', enum: ['upheld', 'refuted', 'revised'] },
    evidence: { type: 'string' },
    note: { type: 'string' },
  },
  required: ['target', 'verdict', 'evidence', 'note'],
  additionalProperties: false,
}
// args = { mode?: 'fanout'|'single', topic, draftSummary,
//          assumptions: [{claim, evidence, challenge}], hardConstraints: [...], reconstruction }
// 防御性 parse：args 经 tool-call 路径可能以 JSON string 到达
const input = typeof args === 'string' ? JSON.parse(args) : args
const EVIDENCE_RULE = '取证要求：优先检索公开文献/官方文档（WebSearch/WebFetch），或读本 repo 源码、commit、配置取证；不要只凭参数化知识反驳。evidence 必须带出处（URL / 文件路径 / commit hash），并注明是一手还是二手转述。引用文献时必须核对其适用域（scope）是否匹配本主题——scope 不匹配的文献不得作为依据。'
const CTX = `草稿摘要：${input.draftSummary}\nhard constraints：${JSON.stringify(input.hardConstraints)}`
if (input.mode === 'single') {
  const r = await agent(
    `主题「${input.topic}」的第一性原理分析需要对抗验证。${CTX}\n逐条核查以下 unvalidated assumptions 与 reconstruction，每条独立给 verdict：\nassumptions：${JSON.stringify(input.assumptions)}\nreconstruction：「${input.reconstruction}」\n你是 skeptic：对每条 assumption 尽力反驳其分类（找证据证明它实为 hard constraint、已被证实或已被证伪）；对 reconstruction 尝试从同样的约束推出更简或显著不同的方案。${EVIDENCE_RULE} 证据不足以推翻时 verdict=upheld；成立但表述需修正时 verdict=revised。target 填 assumption 序号（"assumption-1"…）或 "reconstruction"。`,
    { label: 'verify:single-pass', phase: 'Refute',
      schema: { type: 'object', properties: { verdicts: { type: 'array', items: VERDICT } }, required: ['verdicts'], additionalProperties: false } })
  return r.verdicts
}
const tasks = input.assumptions.map((a, i) => () =>
  agent(`主题「${input.topic}」的第一性原理分析中，这条被分类为 unvalidated assumption：「${a.claim}」（evidence：${a.evidence || '无'}；challenge：${a.challenge}）。${CTX}\n你是 skeptic：尽力反驳这个分类——找证据证明它实为 hard constraint、已被证实或已被证伪。${EVIDENCE_RULE} 证据不足以推翻时 verdict=upheld；分类成立但表述需修正时 verdict=revised。target 填「assumption-${i + 1}」。`,
    { label: `refute:assumption-${i + 1}`, phase: 'Refute', schema: VERDICT }))
tasks.push(() =>
  agent(`主题「${input.topic}」的第一性原理分析给出 hard constraints：${JSON.stringify(input.hardConstraints)}，并据此重建方案：「${input.reconstruction}」。${CTX}\n你是 skeptic：尝试从同样的约束推出更简或显著不同的方案，反驳该方案的「最简有效」性。${EVIDENCE_RULE} 推不出更优方案则 verdict=upheld。target 填「reconstruction」。`,
    { label: 'refute:reconstruction', phase: 'Refute', schema: VERDICT }))
return await parallel(tasks)
```

裁决处理规则：

- `refuted` / `revised` 的条目**必须**修订正文（重新分类、改 challenge 或改方案），不得静默忽略。
- 全部 verdict 与处理方式写入文档 `## Adversarial Review` 一节。
- 即使草稿没有任何 assumption，也必须对 Reconstruction 跑 skeptic（所以 Adversarial Review 永远非空）。

### Phase 3 — 修订并落盘

按 `templates/fpa-doc.md` 写 `FPA-<topic-slug>.md`（slug 用英文 kebab-case）。

同级落盘 `adversarial-verdicts.json`：全部 verdict 原文（含完整 evidence / note）。正文 Adversarial Review 表只是压缩摘要；取证细节若只留在临时 task output 会随系统清理丢失，References 复核 skeptic 转述时以此为原料。

证据可信度规则（写入文档时强制）：

- skeptic 提供的外部证据逐条标注 `source_type` 与 `credibility`，写进文末 `# References`。
- 一手出处（官方文档、论文原文、repo 源码/commit）且已核实 → 可标 high；**skeptic 转述而未复核原文的二手数据封顶 medium**，并显式注明「未逐一复核原文」。
- credibility ≤ medium 的数据不得作为 Key Insight 的唯一支撑；若不可避免，Key Insight 中显式带不确定性措辞。

### Phase 4 — 机械验收

```bash
python3 <本 skill base dir>/scripts/validate_fpa.py <FPA 文件路径>
```

plugin 自带 PostToolUse hook（`hooks/hooks.json`），安装即生效，按文件名分两档自动校验（被 block 时按 stderr 指出的缺失项修复后重写）：

- Write/Edit `FPA-*.md` → 结构校验（frontmatter、六 section、表格不变量）；
- Write/Edit `RUN-REPORT-*.md`（Phase 5 落盘时触发）→ **三件套 suite 校验**：同级同 slug 的 `FPA-*.md` 结构通过 + `adversarial-verdicts.json` 存在且 verdict 原文条数 ≥ 正文 Adversarial Review 表行数。过程合规由此转化为「过程产物链可机械验证」，不依赖执行自觉；挂在 run report（最后一个产物）上是为了不误伤 Phase 3→5 之间的中间状态。

### Phase 5 — Run Report（必出）

按 `templates/fpa-run-report.md` 渲染本次运行汇总：

- **对话内必须输出全文**——分析跑完没有向用户呈报等于没跑完；
- 同时落盘 `RUN-REPORT-<topic-slug>.md`（与 FPA 文档同级，**slug 必须与 FPA 文档一致**——suite 校验按 slug 配对）；
- 结构固定：需求拆解 → 裁决汇总（含 upheld/revised/refuted 计数行）→ 草稿→终稿关键变化 → Key Insight → 遗留实验/下一步；
- 术语规约：列名用读者无需上下文即可理解的明白话（「现有方案中由什么满足」「FPA 文档据此改了什么」），禁止内部行话漏出。

## 验收标准

机械（validate 脚本强制）：frontmatter 完整、六个必需 section 齐全、Deconstruction 含需求拆解表、约束表与 Adversarial Review 非空、Key Insight 非空、文末 `# References` 至少一条；run report 落盘时追加三件套 suite 校验（FPA 文档结构 + verdicts 原文存在 + 条数不少于正文裁决行数）。

语义（自检）：一份合格的 FPA 必须包含三样东西——约束分类表、**被推翻或被对抗检验过的具体假设**、从真实约束重建的方案。缺一即伪分析。运行结束时三件套产物必须齐全（`FPA-*.md` / `adversarial-verdicts.json` / `RUN-REPORT-*.md`），且 run report 已在对话内呈给用户。

# References

- `../first-principles-thinking/SKILL.md`（方法定义 source of truth，同 plugin）
- `templates/fpa-doc.md`（落盘文档模板）
- `templates/fpa-run-report.md`（run report 模板）
- `scripts/validate_fpa.py`（机械验收，CLI + hook 双模式）
- Kamoi et al., When Can LLMs Actually Correct Their Own Mistakes?（arXiv:2406.01297）——Phase 2 双档验证的设计依据
