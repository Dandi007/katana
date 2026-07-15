---
name: deep-research
description: 大规模多源探索并生成研究综述。通过 wiki/work-folder MCP、web 与 config 声明的命名源多源探索；不用于单概念快速解释。
---

# 深度研究助手

对用户问题做大规模系统性探索，产出可长期复用、带引用的知识资产。**编排已 Workflow 化**：流程形状固化为脚本，判断留给 agent 节点；停止由判断驱动，绝不因成本停。

## 配置

研究产物属于已迁移 wiki 域，只使用 `DeepThought/<主题>/` 逻辑路径和
wiki MCP `fs_*`，不解析或暴露 client 上的知识库物理根。

### 命名源（平台源，可选）

`deep_research_sources` 声明 KB 可用的命名信息源（飞书/GitLab/Linear/GitHub/任意），探索时 worker 可按线索性质检索这些源：

```
deep_research_sources=feishu:/retrieval:feishu,gitlab:/retrieval:gitlab,linear:/retrieval:linear,github:/retrieval:github,reddit:/retrieval:reddit,web:/retrieval:web,code:/retrieval:code
```

- 格式：逗号分隔的 `name:entry` 对，每段按**第一个冒号**切分
- `entry`：`/retrieval:<name>` 形式的 retrieval plugin 源入口（调用 `/retrieval:<name>` 即可，fallback 梯度与可信度由 retrieval plugin 承载）
- 优先级：env `DEEP_RESEARCH_SOURCES` → 项目根 `.katana` → 未配置（行为与纯 KB+web 完全一致）
- **源语义不进 config**：什么时候用、怎么检索、fallback 策略，由 retrieval plugin 的对应源承载

### fan-out 宽度（可选）

`deep_research_max_width`：每轮并行探索的线索数上限。优先级：env `DEEP_RESEARCH_MAX_WIDTH` → `.katana` → 默认 10。同时刻真实并发受 Workflow harness `min(16, CPU核数-2)` 约束，超出排队执行不丢失。

### 模型档位（可选）

`deep_research_models`：四类 agent 节点各自跑哪个模型档。格式同 `deep_research_sources`，逗号分隔 `name:model`（按第一个冒号切分），model 取值 `opus`/`sonnet`/`haiku`/`fable`：

```
deep_research_models=worker:sonnet,triage:opus,synth:opus,harvest:haiku
```

| 节点 | 默认 | 作用 | 为什么这个档 |
|------|------|------|------------|
| `worker` | `sonnet` | per-source 检索 + 写 L2 原文，量大、并行、成本敏感 | 检索为主，sonnet 够用且便宜；可按 topic 意图上下调（见 Stage A） |
| `triage` | `opus` | 判断收敛 + 选下一轮 frontier | 判断质量直接决定停不停、追什么，给最强档 |
| `harvest` | `haiku` | `fs_glob` + `fs_read` 汇编 findings index | 纯 MCP IO，haiku 够用且省 token |
| `synth` | `opus` | 索引驱动选择性读 L2 + 写终稿 report | 综合叙事质量最重，给最强档 |

- 优先级：env `DEEP_RESEARCH_MODELS` → `.katana` → 默认。缺省或非法值由 workflow 逐档回退到上表默认。
- **档位在启动 workflow 前定好**（Stage A 主 agent 那一次），workflow 内每轮不变——模型在 `agent()` spawn 时即绑定，被 spawn 的 worker 无法自己改。

## 调用
```
/deep-research <问题或线索>
```

## 执行流程（四段式）

### A. 对话预备阶段（主 agent，轻）
1. `date "+%Y-%m-%d %H:%M"` 确认时间。
2. 解析输入 → 生成可读自然语言主题名（空格分隔，如「PPO vs SAC 对比」）。
3. 生成 wiki 逻辑路径 `DeepThought/<主题名>`（或使用用户显式指定的该域内
   `topicPath`）。用 wiki MCP `fs_create` 建主题目录与 `findings/`；需了解 wiki
   结构时用 `fs_read("WIKI.md")`，不得读取 client 物理路径。
   同时按优先级读取 `deep_research_sources` / `deep_research_max_width` /
   `deep_research_models`（env → `.katana` → 默认），解析命名源、宽度和模型。
   **worker 档按 topic 意图定**（启动后不可变）：纯事实聚合类下调
   `worker:haiku`；技术深挖、读代码或辨析冲突证据上调 `worker:opus`；拿不准用
   `sonnet`。triage/synth 一般保持 `opus`，除非用户另有指定。
4. 把输入拆成 3-6 条初始线索，每条形如
   `{ id:"c0", text:"...", local:<bool>, suggested_sources:[...], depth:0 }`。
    suggested_sources 可选：①`wiki`（`wiki_search`）②`work-folder`（`wf_search`）
    ③`web` ④已声明命名源名。命名源 entry 是 `/retrieval:<name>` 入口，绝不是
    文件路径；fallback 与可信度由 retrieval plugin 承载。
   **不强制澄清提问**（Workflow 中途问不了，低摩擦直接跑；仅当输入完全无法解析时才追问）。

### B. 调用 Workflow（后台跑 BFS + harvest + 综合）
调用 Workflow 工具：
```
Workflow({
  scriptPath: "<本 skill 的 base directory>/workflow.js",  // Skill 加载时给出 base directory，填绝对路径
  args: { topic: "<原问题>", topicPath: "DeepThought/<主题名>",
          skillDir: "<本 skill 的 base directory 绝对路径>",
          sources: { ...阶段A解析的命名源映射，无则传 {} }, maxWidth: <阶段A解析的宽度，未配置则省略>,
          models: { worker: "<档>", triage: "<档>", synth: "<档>" },  // 阶段A定好的三档，缺省档省略由 workflow 回退默认
          initialClues: [ ...上面拆的线索 ] }
  // topicPath 必须是 DeepThought/ 下的 wiki MCP 逻辑路径
})
```
（本 skill 指令即 Workflow 的合法 opt-in。）Workflow 会一轮轮 fan-out worker、triage 判断收敛、最后 synthesis 写产物。期间可 `/workflows` 看进度、随时 kill。

**MUST：只能用 `scriptPath` 形式调用本 skill 的 workflow.js。绝不能用 `Workflow({name: "deep-research"})`**——环境中可能存在同名的通用 named workflow（web 对抗验证语义，与本 skill 的 BFS clue 流程完全不同），按 name 调用会被它劫持，产物（clue_board / findings L2 / sources / topics）全部缺失。判别正确执行：`DeepThought/<主题>/findings/r*-c*.md` 必须随轮次产生。

### C. 对话收尾阶段（主 agent）
Workflow 返回后：展示 Executive Summary + Key Takeaways；提议
①扩充某条线索（重新发起一次 Workflow）②把 topics.md 中的种子提炼为知识库笔记（遵循 KB 自身的笔记约定，如有）。

## 产物（wiki MCP 逻辑路径 `DeepThought/<主题>/`）

`clue_board.md`（triage 写的快照）· `findings/r{n}-c{id}__<source>.md`（worker 按源写的 per-source L1+L2）· `findings/index.md`（harvester 汇编的索引表）· `sources.md`/`topics.md`/`report.md`（synthesis 写）。

### per-source 文件与 reports[] 契约

- **per-source 文件名**：`findings/r{round}-c{clue.id}__<源名>.md`，每个源一个文件，frontmatter 含 `source / anchor / evidence_credibility / digest`。
- **worker 回传 reports[]**：每项含 `source / anchor / evidence_credibility / digest / l2_file`。
- **harvester 节点**：用 wiki MCP `fs_glob` + `fs_read` 扫 findings frontmatter，以 `fs_write` 写 `findings/index.md`。
- **synth 索引驱动**：先 `fs_read` index，按 evidence_credibility/relevance 选择性读 L2。

## 停止语义
停 = triage `converged=true`（主问题已可充分回答）或 frontier 枯竭。**绝不因轮数/预算/成本停**；`SAFETY_CAP` 仅防脚本失控。

## 恢复未完成研究
用 wiki MCP `fs_glob` / `fs_read` 检查 `DeepThought/<主题>/`：有 clue_board
则从 Frontier 重建线索；有 topics 无 report 则只综合；有 findings/index.md 可跳过 Harvest。

## 通用规则
- 探索源只读；wiki 域只用 `wiki_search`/`fs_read`，工作记录只用 `wf_search`/work-folder `fs_read`，未迁子树才可用 `/retrieval:search-note|code`。
- 外部源优先经 `/retrieval:<source>`，无则 fallback WebSearch/WebFetch / 平台只读 CLI。
- 来源标注 `[本地]/[互联网]/[平台:<源名>]/[AI]`；可信度 high/medium/low/conflicted。

## 模板
| 模板 | 用途 | 写入者 |
|------|------|--------|
| templates/finding.md | per-source L1+L2 原始素材（单源卡片头：source/evidence_credibility/digest） | worker |
| templates/clue_board.md | 线索快照 | triage agent |
| templates/sources.md / topics.md / report.md | 最终产物 | synthesis agent |
