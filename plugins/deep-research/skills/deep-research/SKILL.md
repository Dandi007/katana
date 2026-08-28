---
name: deep-research
description: 大规模多源探索并生成研究综述。产物落研究专属 work folder（work-folder MCP）；经 wiki/work-folder MCP、web 与 config 声明的命名源多源探索；不用于单概念快速解释。
---

# 深度研究助手

对用户问题做大规模系统性探索，产出可长期复用、带引用的知识资产。**编排已 Workflow 化**：流程形状固化为脚本，判断留给 agent 节点；停止由判断驱动，绝不因成本停。

## 配置

研究产物落 **work-folder 域**：每次研究一个专属 work folder（Stage A 经
`wf_create` 建，topic 形如 `deep-research: <主题名>`），所有文件经 work-folder
MCP `fs_*` 以 folder 相对路径读写；`folder_id` 是 opaque token，只从
`wf_create`/`wf_search`/`wf_list` 返回值取得，不解析、不拼接、不暴露物理路径。
wiki 域只作**检索源**（katana-wiki-mcp `search` / `page_get`），本 skill 不写 wiki。

> 历史：0.6.x 及以前产物写 wiki MCP `fs_*` 的 `DeepThought/<主题>/`；2026-08-27
> wiki-v3 cutover 后该接口不存在（新 wiki MCP 无 `fs_*`，DeepThought 只读归档），
> 0.7.0 起落位迁至 work folder。恢复旧研究见「恢复未完成研究」。

### 命名源（平台源，可选）

`deep_research_sources` 声明 KB 可用的命名信息源（飞书/GitLab/Linear/GitHub/任意），探索时 worker 可按线索性质检索这些源：

```
deep_research_sources=feishu:/retrieval:feishu,gitlab:/retrieval:gitlab,linear:/retrieval:linear,github:/retrieval:github,reddit:/retrieval:reddit,code:/retrieval:code
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
| `harvest` | `haiku` | `fs_list` + `fs_read` 汇编 findings index | 纯 MCP IO，haiku 够用且省 token |
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
3. 经 work-folder MCP `wf_create(topic="deep-research: <主题名>")` 建研究专属
   work folder，记下返回的 `folder_id`。用户要求续接已有研究时，用
   `wf_search`/`wf_list` 取该研究 folder 的 id，不新建。
   同时按优先级读取 `deep_research_sources` / `deep_research_max_width` /
   `deep_research_models`（env → `.katana` → 默认），解析命名源、宽度和模型。
   **worker 档按 topic 意图定**（启动后不可变）：纯事实聚合类下调
   `worker:haiku`；技术深挖、读代码或辨析冲突证据上调 `worker:opus`；拿不准用
   `sonnet`。triage/synth 一般保持 `opus`，除非用户另有指定。
4. 把输入拆成 3-6 条初始线索，每条形如
   `{ id:"c0", text:"...", local:<bool>, suggested_sources:[...], depth:0 }`。
    suggested_sources 可选：①`wiki`（katana-wiki-mcp `search`）②`work-folder`（`wf_search`）
    ③`web` ④已声明命名源名。命名源 entry 是 `/retrieval:<name>` 入口，绝不是
    文件路径；fallback 与可信度由 retrieval plugin 承载。
   **不强制澄清提问**（Workflow 中途问不了，低摩擦直接跑；仅当输入完全无法解析时才追问）。

### B. 调用 Workflow（后台跑 BFS + harvest + 综合）
调用 Workflow 工具：
```
Workflow({
  scriptPath: "<本 skill 的 base directory>/workflow.js",  // Skill 加载时给出 base directory，填绝对路径
  args: { topic: "<原问题>", folderId: "<Stage A 拿到的 folder_id>",
          skillDir: "<本 skill 的 base directory 绝对路径>",
          sources: { ...阶段A解析的命名源映射，无则传 {} }, maxWidth: <阶段A解析的宽度，未配置则省略>,
          models: { worker: "<档>", triage: "<档>", synth: "<档>" },  // 阶段A定好的三档，缺省档省略由 workflow 回退默认
          initialClues: [ ...上面拆的线索 ] }
  // folderId 必须是 work-folder MCP 签发的 opaque id；不传时 workflow 的
  // Setup 节点会自行 wf_create（返回值里带 folderId）
})
```
（本 skill 指令即 Workflow 的合法 opt-in。）Workflow 会一轮轮 fan-out worker、triage 判断收敛、最后 synthesis 写产物。期间可 `/workflows` 看进度、随时 kill。

**MUST：只能用 `scriptPath` 形式调用本 skill 的 workflow.js。绝不能用 `Workflow({name: "deep-research"})`**——环境中可能存在同名的通用 named workflow（web 对抗验证语义，与本 skill 的 BFS clue 流程完全不同），按 name 调用会被它劫持，产物（clue_board / findings L2 / sources / topics）全部缺失。判别正确执行：研究 folder 内 `findings/r*-c*.md`（work-folder MCP `fs_list` 可见）必须随轮次产生。

### C. 对话收尾阶段（主 agent）
Workflow 返回 `{ folderId, synthesis }` 后：展示 Executive Summary + Key Takeaways；用
`wf_save` 给研究 folder 存档 checkpoint；提议
①扩充某条线索（对同一 `folderId` 重新发起一次 Workflow）②把 report/topics 的结论交
wiki `ingest_submit` 入库（librarian 判重、归类、落库为成果快照页——不要自行判重手工建页）。

## 产物（研究 work folder 内，经 work-folder MCP 读写）

`clue_board.md`（triage 写的快照）· `findings/r{n}-c{id}__<source>.md`（worker 按源写的 per-source L1+L2）· `findings/index.md`（harvester 汇编的索引表）· `sources.md`/`topics.md`/`report.md`（synthesis 写）。路径均为 folder 相对路径，配合 `folder_id` 寻址。

### per-source 文件与 reports[] 契约

- **per-source 文件名**：`findings/r{round}-c{clue.id}__<源名>.md`，每个源一个文件，frontmatter 含 `source / anchor / evidence_credibility / digest`。
- **worker 回传 reports[]**：每项含 `source / anchor / evidence_credibility / digest / l2_file`。
- **harvester 节点**：用 work-folder MCP `fs_list` + `fs_read` 扫 findings frontmatter，以 `fs_create`/`fs_write` 写 `findings/index.md`。
- **synth 索引驱动**：先 `fs_read` index，按 evidence_credibility/relevance 选择性读 L2。
- 新文件用 `fs_create`；`fs_write` 只覆盖已存在文件，不隐式创建。

## 停止语义
停 = triage `converged=true`（主问题已可充分回答）或 frontier 枯竭。**绝不因轮数/预算/成本停**；`SAFETY_CAP` 仅防脚本失控。

## 恢复未完成研究
用 `wf_search`（关键词 `deep-research: <主题名>`）定位研究 folder，`fs_list` / `fs_read`
检查：有 clue_board 则从 Frontier 重建线索（对同一 `folderId` 重新发起 Workflow）；
有 topics 无 report 则只综合；有 findings/index.md 可跳过 Harvest。
0.6.x 时代写在旧 wiki 库 `DeepThought/` 的历史研究是只读归档，不在本流程内续跑。

## 通用规则
- 探索源只读；wiki 域只用 katana-wiki-mcp `search`/`page_get`，工作记录只用 `wf_search`/work-folder `fs_read`，未迁子树（含 `DeepThought/`、`转换文档/`）才可用 `/retrieval:search-note|code`。
- 本研究的 work folder 是唯一 mutation 面；不写 wiki、不发消息、不评论 issue/MR、不 push。
- 外部源优先经 `/retrieval:<source>`，无则 fallback WebSearch/WebFetch / 平台只读 CLI。
- 来源标注 `[本地]/[互联网]/[平台:<源名>]/[AI]`；可信度 high/medium/low/conflicted。

## 模板
| 模板 | 用途 | 写入者 |
|------|------|--------|
| templates/finding.md | per-source L1+L2 原始素材（单源卡片头：source/evidence_credibility/digest） | worker |
| templates/clue_board.md | 线索快照 | triage agent |
| templates/sources.md / topics.md / report.md | 最终产物 | synthesis agent |
