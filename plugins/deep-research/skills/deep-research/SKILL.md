---
name: deep-research
description: 大规模多源探索并生成研究综述。本地知识库（环境变量 DEEP_RESEARCH_KB_DIR 指定根目录，默认当前目录）与 web 双源探索；不用于单概念快速解释。
---

# 深度研究助手

对用户问题做大规模系统性探索，产出可长期复用、带引用的知识资产。**编排已 Workflow 化**：流程形状固化为脚本，判断留给 agent 节点；停止由判断驱动，绝不因成本停。

## 调用
```
/deep-research <问题或线索>
```

## 执行流程（三段式）

### A. 对话预备阶段（主 agent，轻）
1. `date "+%Y-%m-%d %H:%M"` 确认时间。
2. 解析输入 → 生成可读自然语言主题名（空格分隔，如「PPO vs SAC 对比」）。
3. 确定知识库根 `KB=${DEEP_RESEARCH_KB_DIR:-当前目录}`，先读 `$KB/CLAUDE.md` 或 `$KB/AGENTS.md`（如存在）了解库结构与检索约定；建目录 `$KB/DeepThought/<主题名>/` 与 `$KB/DeepThought/<主题名>/findings/`。
4. 把输入拆成 3-6 条初始线索，每条形如
   `{ id:"c0", text:"...", local:<bool>, suggested_sources:[...], depth:0 }`。
   判断源方向：知识库内可答→local=true（suggested_sources 填 KB 内子目录）、需外部信息→web(local=false)。
   **不强制澄清提问**（Workflow 中途问不了，低摩擦直接跑；仅当输入完全无法解析时才追问）。

### B. 调用 Workflow（后台跑 BFS + 综合）
调用 Workflow 工具：
```
Workflow({
  scriptPath: "<本 skill 的 base directory>/workflow.js",  // Skill 加载时给出 base directory，填绝对路径
  args: { topic: "<原问题>", topicDir: "DeepThought/<主题名>", skillDir: "<本 skill 的 base directory 绝对路径>", initialClues: [ ...上面拆的线索 ] }
})
```
（本 skill 指令即 Workflow 的合法 opt-in。）Workflow 会一轮轮 fan-out worker、triage 判断收敛、最后 synthesis 写产物。期间可 `/workflows` 看进度、随时 kill。

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
- 来源标注 `[本地]/[互联网]/[AI]`；可信度 high/medium/low/conflicted。

## 模板
| 模板 | 用途 | 写入者 |
|------|------|--------|
| templates/finding.md | L1+L2 原始素材 | worker |
| templates/clue_board.md | 线索快照 | triage agent |
| templates/sources.md / topics.md / report.md | 最终产物 | synthesis agent |
