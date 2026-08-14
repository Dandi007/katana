# 001 — 开发纪律（katana）

**Status:** Active
**Scope:** 本仓所有改动，人与 agent 一视同仁
**约定:** 本文件记录**硬线**——违反即 REJECT。可被实现完成的内容属于 `docs/specs/`，不写在这里。

## 1. 分支与合入

1. **`main` 只经 PR 合入**，且必须有人工审核代合。禁止直接 push `main`。
2. **dev-dispatch 单的 base 禁止是 `main`**（生态宪法第十条）。dd 单从任务分支起，再由 PR 进 `main`。
3. **不在主 checkout（`/data/code/self/katana`）上开新分支干活**；新工作一律另起 worktree。
4. 发布走 `release/<name>` 分支合入 `main`，npm 包由 `.github/workflows/npm-publish.yml` 发布。

## 2. 验收面（CI 三关，全部 fail-closed）

5. `.github/workflows/g0.yml`（**g0-structural-gate**）、`tests.yml`、`npm-publish.yml` 是本仓的验收与发布面。**不得为了让某关变绿而放宽它自身的判据**。
6. 本地对应入口：`npm test`（`bun test parity/adapter/opencode/`）、`npm run test:shell`、`npm run test:pack`、`npm run lint`（`tests/lint-structure.sh`）、`npm run e2e`。
7. `tests/lint-structure.sh` 守的是**目录结构契约**。新增 plugin 必须满足它，不得靠改 lint 来通过。
8. `package.json` 的 `files` allowlist 决定 npm tarball 内容。新增需要随包分发的路径必须显式加进去；反过来，**不在 allowlist 里的路径改动不影响发布产物**。

## 3. 生产与运行边界

9. 本仓是**生产服务仓**（`mcp/work-folder` 的 Work Folder MCP 在生产运行）。**只改文档的 PR 不触发也不需要部署或重启。**
10. Work Folder flat cutover 必须在**服务离线、Git HEAD 锁定、repair metadata 完整**时执行；迁移完成但尚未通过 `verify` 与 server startup gate 前，**不得提交或恢复流量**。步骤见 [003](../specs/003-work-folder-flat-cutover-runbook.md)。
11. cutover 的 `inventory` 阶段**只盘点不修改**；结构异常、双根碰撞、symlink、特殊文件、非法 tombstone 或未分类 payload 一律 **fail closed**。
12. 每个 plugin 必须**独立可安装**。plugin 之间不得引入隐式依赖——用户只装其中一个也要能工作。

## 4. 文档

13. 设计/规格/计划/runbook 落**仓根** `docs/specs/`，硬线落 `docs/constitution/`，命名 `NNN-kebab-topic.md`，三位递增、**号码不复用**、两目录独立编号。子包（`mcp/*`、`parity/`）不再各自维护 `docs/specs/`。
14. `plugins/*/skills/**`、`tests/judge/case-rubrics/`、`tests/reports/`、`tests/fixtures/` 下的 markdown 是**产品内容与测试资产**，不是仓库文档，不适用本节命名规则，不得挪动。
15. 文档移动必须 `git mv`（保 git 历史）；迁移时不改写正文，只修被移动打断的 markdown 链接。
16. 根 [AGENTS.md](../../AGENTS.md) 是 agent 的 canonical 入口，只做导航；新增文档必须同步登记进它的文档地图。
