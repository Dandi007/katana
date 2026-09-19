# Contract Sweep Report

- branch: `chore/remove-wiki-plugin` @ `8deb75c`
- date: 2026-09-19 17:15
- jobs: 4 / total: 505s
- **PASS 9 / FAIL 0 / SKIP 9 / NEEDS-REVIEW 3**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| feishu-docs:feishu-docs#feishu-docs-config | PASS | — | 1 | 186s | glm-5.3 |  |
| retrieval:code#code-local-repo | SKIP | — | 0 | 0s | glm-5.3 | dir missing: /Volumes/Data/code/self/katana |
| retrieval:feishu#feishu-doc-search | PASS | — | 1 | 251s | glm-5.3 |  |
| retrieval:github#github-repo-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:gitlab#gitlab-project-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:linear#linear-issue-query | SKIP | — | 0 | 0s | glm-5.3 | env LINEAR_API_KEY unset |
| retrieval:official-docs#official-docs-lookup | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:reddit#reddit-search | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:route#route-three-queries | PASS | — | 1 | 150s | glm-5.3 |  |
| retrieval:search-note#search-note-local | PASS | — | 1 | 68s | glm-5.3 |  |
| retrieval:twitter#twitter-fetch | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:web#web-fetch | SKIP | — | 0 | 0s | glm-5.3 | env KATANA_E2E_NETWORK unset |
| retrieval:xiaohongshu#xiaohongshu-download | SKIP | — | 0 | 0s | glm-5.3 | dir missing: $KATANA_TEST_XHS_PROFILE |
| work-folder:checkpoint#checkpoint-resume | PASS | — | 1 | 67s | glm-5.3 |  |
| work-folder:checkpoint#checkpoint-save | PASS | — | 1 | 100s | glm-5.3 |  |
| writing:bluf#bluf-structure | PASS | — | 1 | 130s | glm-5.3 |  |
| writing:readability-check#distill-mode | NEEDS-REVIEW | — | 1 | 92s | glm-5.3 | semantic judge non-PASS |
| writing:readability-check#evolve-triage | PASS | — | 1 | 151s | glm-5.3 |  |
| writing:readability-check#readability-check-workflow | PASS | — | 1 | 83s | glm-5.3 |  |
| writing:write#write-smoke | NEEDS-REVIEW | — | 1 | 224s | glm-5.3 | semantic judge non-PASS |
| writing:write#write-template-instantiate | NEEDS-REVIEW | — | 1 | 101s | glm-5.3 | semantic judge non-PASS |

## Skipped

- retrieval:code#code-local-repo: dir missing: /Volumes/Data/code/self/katana
- retrieval:github#github-repo-lookup: env KATANA_E2E_NETWORK unset
- retrieval:gitlab#gitlab-project-lookup: env KATANA_E2E_NETWORK unset
- retrieval:linear#linear-issue-query: env LINEAR_API_KEY unset
- retrieval:official-docs#official-docs-lookup: env KATANA_E2E_NETWORK unset
- retrieval:reddit#reddit-search: env KATANA_E2E_NETWORK unset
- retrieval:twitter#twitter-fetch: env KATANA_E2E_NETWORK unset
- retrieval:web#web-fetch: env KATANA_E2E_NETWORK unset
- retrieval:xiaohongshu#xiaohongshu-download: dir missing: $KATANA_TEST_XHS_PROFILE

## NEEDS-REVIEW

### writing:readability-check#distill-mode
- [no] 回答是否提及 distill 模式（或含 "distill" 字样）？ — trace 中不存在 agent 回答文本：仅有 SessionStart hook 事件（如 hook_name:"SessionStart:startup"）与 thinking_tokens 系统事件，且在 'subtype":"thinking_' 处截断；无任何 assistant 回答行含 "distill"。
- [no] 回答是否说明 distill 会产出 template 类文件（写前骨架）？ — trace 中无 agent 回答可引用；全文没有任何行提及 distill 产出 template/写前骨架。
- [no] 回答是否说明 distill 会产出 pattern 类文件（审前评判标准）？ — trace 中无 agent 回答可引用；全文没有任何行提及 distill 产出 pattern/审前评判标准。
- [no] 回答是否提及落盘前需要人工确认（或等价表述：不自动写入、须用户批准）？ — trace 中无 agent 回答可引用。唯一含 "人工确认" 的是 SessionStart hook 注入的系统上下文（"进化 gate（raw 不可变、人工确认防 model-collapse）随 /writing:* 调用载入"），属 hook 注入而非 agent 回答原文，不计入。
### writing:write#write-smoke
- [yes] 回答是否提及写前需读取 patterns 相关文件（.katana-writing/patterns/ 或等价）？ — 写前第 5 步：「随后读 `patterns/` 同 type 的「适用判定/反模式」做校准」，位于「## 写前（动笔正文之前）」节内，与 skill 写中约定的「随后读 `patterns/<type>.md` 的「适用判定/反模式」做校准」等价对应。
- [yes] 回答是否提及写后需要做自检（或等价：self-check、对照 pattern 检查）？ — 「## 写后（正文完成之后）1. **执行固定格式 AI 自检** — 按既定 schema 输出「自检结果」（目标对齐 / 读者适配 / 结构完整性 / BLUF 合规 / 读者主线清晰 / 表达密度 / 历史规则符合度）+「问题清单」（每条标 high/medium/low 及处理方式）」，七项检查维度与 skill 自检 schema 逐项一致。
- [yes] 写前步骤和写后步骤均有清晰描述，未混淆顺序？ — 回答以「## 写前（动笔正文之前）」（5 个编号步骤：errors.md→任务识别→检索改进卡片→合并 checklist→实例化 template）与「## 写后（正文完成之后）」（3 个编号步骤：自检→卡片判定→反馈沉淀）两节清晰分列，先后顺序与 skill 的「写前 workflow→写中→写后 workflow」一致，无前置后置错置。
- [no] 描述与 writing:write skill 实际约定一致（无幻觉步骤）？ — 回答断言「tech-spec 属于已固化 per-kind template 的类型（`template/tech-spec.md` + `patterns/` 校准）」且「tech-spec 有 template，所以先原样 emit `template/tech-spec.md` 的 `## Layout` 骨架」。但 skill 原文将 tech-spec 标注为已固化「（pattern 层）」，template 分支为条件式「**若**当前项目 writing_dir 下的 `template/<kind>.md` 存在」，且规定了无 template 命中的回退分支（读 `patterns/<type>.md` 残留骨架→无 pattern 则按 bluf L0–L3 + offer distill）。实际 writing_dir（kb/writing/）`template/` 下仅有 atomic-note.md、`patterns/` 为空——`template/tech-spec.md` 不存在，回答描述了不成立的分支，属对实际约定的误读（其余写前/写后步骤均与 skill 逐条吻合，无其它幻觉步骤）。
### writing:write#write-template-instantiate
- [no] 回答是否提及命中 kind 后会读取对应 template 文件来搭骨架？ — trace 中不存在任何回答正文：仅有 hook_response（Work Folder MCP / using-retrieval / using-writing 注入）、init 元数据和 thinking_tokens 计数，无 assistant 文本提及『读取 template 文件搭骨架』。
- [no] 回答是否说明产出结构由 Layout（template 骨架）来保证？ — trace 中无任何回答文本提及 Layout 或 template 骨架保证产出结构；可见内容仅为系统 hook 注入与会话初始化记录。
- [no] 回答是否提及没有对应 template 时的回退策略（走 distill 冷启动或等价）？ — trace 中无回答正文，也就无从提及 distill 冷启动或任何回退策略；trace 截断于 thinking_tokens 事件（uuid 前缀 a0b5ed）。
- [no] 整体流程描述与 writing:write skill 实际约定一致（无幻觉步骤）？ — trace 不含任何对 writing:write 流程的描述文本（唯一相关内容是 hook 注入的『写/改任何文档先走 /writing:write、先实例化对应 template，勿裸写』，属系统上下文而非 agent 回答），无整体流程描述可判定一致性。

## Overall Verdict

_(not run)_
