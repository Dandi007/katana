# AGENTS.md — katana

A sharp little toolkit of agent plugins（Claude Code + OpenCode 双端）：每个 plugin 独立可安装，另含 OpenCode parity 适配层与 Work Folder MCP 服务。

## 关键入口

- **plugins/**：13 个独立 plugin（`guide` / `work-folder` / `deep-research` / `memory` / `obsidian-md` / `wiki` / `retrieval` / `fpa` / `writing` / `jury` / `incubate` / `feishu-docs` / `agent-skills`）。安装方式见 [README.md](README.md)。
- **parity/**：OpenCode parity 适配层，npm 包 `opencode-katana`（`package.json` 的 `files` allowlist 决定分发内容）。
- **mcp/work-folder/**：Work Folder MCP（Python，`katana_work_folder_mcp`），生产服务；CLI `wf-report`，迁移工具 `scripts/migrate_flat.py`。
- **验收命令**：`npm test`（`bun test parity/adapter/opencode/`）、`npm run test:shell`、`npm run test:pack`、`npm run lint`、`npm run e2e`。
- **CI**：`.github/workflows/g0.yml`（g0-structural-gate）、`tests.yml`、`npm-publish.yml`。

## 文档地图

命名 `NNN-kebab-topic.md`，三位递增、号码不复用；`docs/specs/` 与 `docs/constitution/` 独立编号。

**[`docs/constitution/`](docs/constitution/)** —— 硬线、纪律、不变量，违反即 REJECT。

- [001 开发纪律](docs/constitution/001-development-discipline.md) —— 分支与合入、CI 三关验收面、生产与 cutover 边界、文档纪律
- [002 数据面私有](docs/constitution/002-data-plane-privacy.md) —— 三域 data root 只经 MCP：唯一合法写者、只存结论不存运行时产物、爆炸半径匹配作用域、备份与只读运维通道、机检

**[`docs/specs/`](docs/specs/)** —— 设计、规格、spike 结论、runbook。

- [001 OpenCode parity spikes](docs/specs/001-opencode-parity-spikes.md) —— 实现前置的三个 spike 与结论
- [002 Work Folder fs 路径契约虚拟化](docs/specs/002-wf-fs-path-contract-virtualization.md)
- [003 Work Folder flat cutover runbook](docs/specs/003-work-folder-flat-cutover-runbook.md) —— 离线迁移、fail-closed 判据、verify 与 startup gate

**其他（产品内容与测试资产，不是仓库文档，勿挪动）**

- `plugins/*/skills/**` —— 各 plugin 的 SKILL 正文，是产品本体。
- `tests/judge/case-rubrics/`、`tests/judge/overall-rubric.md` —— 评审 rubric。
- `tests/reports/` —— 历次 harness 跑测报告；`tests/fixtures/` —— 测试夹具（含中文 KB 夹具）。

## 开发纪律

细则见 [docs/constitution/001-development-discipline.md](docs/constitution/001-development-discipline.md)，要点：

- 改动一律走 PR；`main` 只经人工审核代合，禁止直接 push。
- **dev-dispatch 单的 base 禁止是 `main`**（生态宪法第十条）。
- 不在主 checkout 上开分支干活，新工作起独立 worktree。
- CI 三关 fail-closed，**不得靠放宽判据变绿**；`tests/lint-structure.sh` 守目录结构契约。
- 本仓是生产服务仓（Work Folder MCP）：**docs-only PR 不需要也不得部署或重启**。
- flat cutover 必须服务离线 + HEAD 锁定 + repair metadata 完整；未过 verify 与 startup gate 前不得恢复流量。
- 每个 plugin 必须独立可安装，不得引入 plugin 间隐式依赖。
- 文档移动必须 `git mv` 保历史；新增文档同步登记进上面的文档地图。
