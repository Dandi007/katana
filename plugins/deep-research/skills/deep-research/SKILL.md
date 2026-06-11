---
name: deep-research
description: 大规模多源探索并生成研究综述。本地知识库（环境变量 DEEP_RESEARCH_KB_DIR 指定根目录，默认当前目录）、web 与 config 声明的命名源多源探索；不用于单概念快速解释。
---

# 深度研究助手

对用户问题做大规模系统性探索，产出可长期复用、带引用的知识资产。**编排已 Workflow 化**：流程形状固化为脚本，判断留给 agent 节点；停止由判断驱动，绝不因成本停。

## 配置

知识库根目录可通过以下方式覆盖（优先级从高到低）：

| 优先级 | 配置方式 | 示例 |
|--------|---------|------|
| 1 | 环境变量 `DEEP_RESEARCH_KB_DIR` | `export DEEP_RESEARCH_KB_DIR=/path/to/kb` |
| 2 | 项目根目录 `.katana` 文件 | `deep_research_kb_dir=.` |
| 3 | 默认值 | 当前工作目录 |

如果项目 `.katana` 文件或环境变量指定了路径，以那个为准，忽略默认值。

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

`deep_research_models`：三类 agent 节点各自跑哪个模型档。格式同 `deep_research_sources`，逗号分隔 `name:model`（按第一个冒号切分），model 取值 `opus`/`sonnet`/`haiku`/`fable`：

```
deep_research_models=worker:sonnet,triage:opus,synth:opus
```

| 节点 | 默认 | 作用 | 为什么这个档 |
|------|------|------|------------|
| `worker` | `sonnet` | 检索 + 写 L2 原文，量大、并行、成本敏感 | 检索为主，sonnet 够用且便宜；可按 topic 意图上下调（见 Stage A） |
| `triage` | `opus` | 判断收敛 + 选下一轮 frontier | 判断质量直接决定停不停、追什么，给最强档 |
| `synth` | `opus` | 合并去重 + 写终稿 report | 综合叙事质量最重，给最强档 |

- 优先级：env `DEEP_RESEARCH_MODELS` → `.katana` → 默认。缺省或非法值由 workflow 逐档回退到上表默认。
- **档位在启动 workflow 前定好**（Stage A 主 agent 那一次），workflow 内每轮不变——模型在 `agent()` spawn 时即绑定，被 spawn 的 worker 无法自己改。

## 调用
```
/deep-research <问题或线索>
```

## 执行流程（三段式）

### A. 对话预备阶段（主 agent，轻）
1. `date "+%Y-%m-%d %H:%M"` 确认时间。
2. 解析输入 → 生成可读自然语言主题名（空格分隔，如「PPO vs SAC 对比」）。
3. 确定知识库根：按优先级读取——环境变量 `DEEP_RESEARCH_KB_DIR` → 项目根 `.katana` 文件的 `deep_research_kb_dir` 值 → 当前目录。先读 `$KB/CLAUDE.md` 或 `$KB/AGENTS.md`（如存在）了解库结构与检索约定；建目录 `$KB/DeepThought/<主题名>/` 与 `$KB/DeepThought/<主题名>/findings/`。
   同时按同优先级读取 `deep_research_sources` / `deep_research_max_width` / `deep_research_models`（env → `.katana` → 默认），解析出命名源映射 `{name: entry}`、宽度值与三档模型 `{worker, triage, synth}`。
   **worker 档按 topic 意图定**（这是「按意图选模型」的唯一时机，启动后不可变）：在解析出的默认基础上判断——纯事实扫库 / 信息聚合类（多数线索是 Grep/Read/简单网页抓取）→ 下调 `worker:haiku` 省成本；技术深挖 / 需要读代码、推理因果、辨析冲突证据的硬研究 → 上调 `worker:opus` 保质量；拿不准就用默认 `sonnet`。triage/synth 一般保持 `opus`，除非用户另有指定。
4. 把输入拆成 3-6 条初始线索，每条形如
   `{ id:"c0", text:"...", local:<bool>, suggested_sources:[...], depth:0 }`。
   suggested_sources 三类可选：①KB 内子目录（此时 local=true）②`web` ③已声明的命名源名（如 `feishu`/`gitlab`/`reddit`/`code`，按线索性质判断——「XX 的群里讨论」→feishu、「XX 的 MR/issue」→gitlab/linear）；②③均 local=false。worker 解析时，每个命名源名（包括 `web`）映射到 `deep_research_sources` 中的对应 entry，即调用 `/retrieval:<name>`——retrieval plugin 自带 fallback 梯度与可信度，worker 无需自行处理降级。
   **不强制澄清提问**（Workflow 中途问不了，低摩擦直接跑；仅当输入完全无法解析时才追问）。

### B. 调用 Workflow（后台跑 BFS + 综合）
调用 Workflow 工具：
```
Workflow({
  scriptPath: "<本 skill 的 base directory>/workflow.js",  // Skill 加载时给出 base directory，填绝对路径
  args: { topic: "<原问题>", topicDir: "<KB根绝对路径>/DeepThought/<主题名>", kbDir: "<KB根绝对路径>",
          skillDir: "<本 skill 的 base directory 绝对路径>",
          sources: { ...阶段A解析的命名源映射，无则传 {} }, maxWidth: <阶段A解析的宽度，未配置则省略>,
          models: { worker: "<档>", triage: "<档>", synth: "<档>" },  // 阶段A定好的三档，缺省档省略由 workflow 回退默认
          initialClues: [ ...上面拆的线索 ] }
  // ⚠️ topicDir / kbDir 必须是绝对路径；workflow subagent 的 CWD 不保证等于项目根
})
```
（本 skill 指令即 Workflow 的合法 opt-in。）Workflow 会一轮轮 fan-out worker、triage 判断收敛、最后 synthesis 写产物。期间可 `/workflows` 看进度、随时 kill。

**MUST：只能用 `scriptPath` 形式调用本 skill 的 workflow.js。绝不能用 `Workflow({name: "deep-research"})`**——环境中可能存在同名的通用 named workflow（web 对抗验证语义，与本 skill 的 BFS clue 流程完全不同），按 name 调用会被它劫持，产物（clue_board / findings L2 / sources / topics）全部缺失。判别正确执行：`DeepThought/<主题>/findings/r*-c*.md` 必须随轮次产生。

### C. 对话收尾阶段（主 agent）
Workflow 返回后：展示 Executive Summary + Key Takeaways；提议
①扩充某条线索（重新发起一次 Workflow）②把 topics.md 中的种子提炼为知识库笔记（遵循 KB 自身的笔记约定，如有）。

## 产物（$KB/DeepThought/<主题>/）
`clue_board.md`（triage 写的快照）· `findings/r{n}-c{id}.md`（worker 写的 L1+L2）· `sources.md`/`topics.md`/`report.md`（synthesis 写）。

## 停止语义
停 = triage `converged=true`（主问题已可充分回答）或 frontier 枯竭。**绝不因轮数/预算/成本停**；`SAFETY_CAP` 仅防脚本失控。

## 恢复未完成研究
读 `DeepThought/<主题>/`：有 `clue_board.md` 快照→从 Frontier 重建 initialClues 再发起 Workflow；有 topics 无 report→只发起 synthesis。

## 通用规则
- 探索路径只读，禁 mutation；本地检索优先遵循 KB 自带的检索约定（CLAUDE.md/AGENTS.md 声明的路由/skill），无约定时用通用文件检索。
- 来源标注 `[本地]/[互联网]/[平台:<源名>]/[AI]`；可信度 high/medium/low/conflicted。

## 模板
| 模板 | 用途 | 写入者 |
|------|------|--------|
| templates/finding.md | L1+L2 原始素材 | worker |
| templates/clue_board.md | 线索快照 | triage agent |
| templates/sources.md / topics.md / report.md | 最终产物 | synthesis agent |
