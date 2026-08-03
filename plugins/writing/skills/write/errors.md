# write 错误记录

## YYYY-MM-DD HH:MM - <错误简述>

**触发任务**：<任务或文档类型>
**症状**：<观察到的异常>
**影响**：<对写作流程的影响>
**根因分析**：<已知原因或待确认原因>
**处理状态**：open | mitigated | resolved
**后续动作**：<是否需要更新 skill 或 wrapper>

---

## 2026-07-11 07:04 - vault 未配置 writing_dir，resolver 返回空路径

**触发任务**：重写 KB MCP 最终态技术设计文档
**症状**：`katana-config.sh resolve writing_dir "" KATANA_WRITING_DIR` 输出空字符串；项目 `.katana` 与环境变量均未声明 `writing_dir`，因此无法定位 `improvements/`、`template/tech-spec.md` 或 `patterns/tech-spec.md`。
**影响**：本次无法加载项目级写作改进卡片与 per-kind template，只能按 `writing:bluf` 与目标 work folder 规范生成文档。
**根因分析**：skill 声称 `writing_dir` 在调用期解析为绝对路径，但当前 resolver 没有内建默认值，vault 也未配置该键。
**处理状态**：mitigated
**后续动作**：为 writing plugin 明确默认 writing_dir，或在 vault `.katana` 中声明；resolver 返回空值时应给出可诊断错误而非静默继续。

---

## 2026-07-11 07:26 - frontmatter 校验命令错误保留 closing delimiter

**触发任务**：重写 KB MCP 最终态技术设计文档后的 YAML 自检
**症状**：命令使用双引号包裹 `sed "$d"`，shell 将未定义变量 `d` 展开为空，导致 closing `---` 未被删除；PyYAML 误报第二个 YAML document。
**影响**：第一次 frontmatter parse 自检失败，文档内容本身未损坏。
**根因分析**：shell quoting 错误；删除最后一行应使用单引号字面量 `sed '$d'`。
**处理状态**：resolved
**后续动作**：frontmatter 校验固定使用 literal single-quoted sed expression，或显式读取两条 delimiter 之间的内容。

---

## 2026-07-11 07:28 - Mermaid CLI 静默模式掩盖不存在的 Puppeteer config

**触发任务**：校验 KB MCP 最终设计中的三个 Mermaid 图
**症状**：`mmdc -p <不存在的 puppeteer.json> --quiet` 未生成 SVG，zsh loop 又未启用 fail-fast，最终没有可见错误输出。
**影响**：第一次图表验收没有实际产物，不能据此声称 Mermaid 有效。
**根因分析**：传入了不存在的 Puppeteer config，且 `--quiet` 与未检查每次 exit code 共同掩盖失败。
**处理状态**：resolved
**后续动作**：使用 `PUPPETEER_EXECUTABLE_PATH=/usr/bin/google-chrome` 并逐文件检查输出；本次三个 Mermaid block 均已成功渲染为 SVG。

---

## 2026-07-12 14:49 - npm cache 有 Mermaid CLI，但 npx --no-install 无法解析 mmdc

**触发任务**：修订 Loop Engine submit-only Runtime 技术设计后的 Mermaid 自检
**症状**：`command -v mmdc` 无结果，`npx --no-install mmdc --version` 返回 `could not determine executable to run`；但 npm cache 中实际存在 `@mermaid-js/mermaid-cli/src/cli.js`。
**影响**：不能使用简写 wrapper 验证 Mermaid，需要改走已安装 CLI 的 exact path。
**根因分析**：当前工作目录没有声明 Mermaid CLI dependency，npm cache package 也没有被 `npx --no-install` 解析成可执行入口。
**处理状态**：mitigated
**后续动作**：本机会话直接以 `node <npm-cache>/@mermaid-js/mermaid-cli/src/cli.js` 调用，并显式设置 `PUPPETEER_EXECUTABLE_PATH=/usr/bin/google-chrome`；后续可在 writing tooling 中提供稳定 wrapper。

---

## 2026-07-12 14:57 - 两次限域 cold-read subagent 均未返回

**触发任务**：修订 Loop Engine submit-only Runtime 技术设计后的可读性冷读
**症状**：先后启动两个只允许读取 `design.md` 的 cold-read subagent，并两次发送“立即收敛/PASS 即可”消息；二者持续处于 running 且无任何输出，最终人工 interrupt。
**影响**：本轮没有取得独立读者反馈，不能声称 cold-read gate 已通过；主 agent 只能完成结构、残留措辞、Mermaid 与页面渲染自检。
**根因分析**：待确认，表现为 collaboration agent turn 长时间无 last_task_message 或 final output，并非目标文件不可读。
**处理状态**：mitigated
**后续动作**：保留现有机械与主 agent 自检证据；后续在 implementation plan 前重试 cold-read，或诊断 collaboration agent 无响应问题。

---
