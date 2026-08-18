# SPEC EK-4 —— audit-evidence 高频大体积产物落点收敛：进 runtime root、仓内留引用（spec 冻结稿）

> 判据来源：`wf-caaeb6/evolution-backlog.md` EK-4（24 份 / 161KB audit-evidence 误进 data repo，与 SOP wf-77510c 落点判据相悖，且 `audit-evidence-r15.md` 恰是 12:21 半落两件产物之一）。
> 交付仓：`Dandi007/katana`，**MR base 只许 `fix/governed-dirty-paths-diagnostic`@e3b6ac52c285e9c83b3f08c34cbe4c8f40fdb1a3，禁 main / 禁 katana-cutover 任何分支**（EK-0 钉死）。
> 派单登记：`wf-caaeb6/dispatch-registry.md` §0/§1；dev 分支 `dd/katana/evidence-runtime`（下附判据先冻结）。
> 与 EK-3 同 work-folder 面 → 串行（EK-3 后）；与 EK-1+EK-2（kernel 面）可并行备 spec。顺序：EK-1+EK-2 → EK-3 → EK-4。
> 本 spec 由 coordinator 编写（wf-caaeb6 线）；实现与 review 全由 dev-dispatch 完成。

---

## 0. 一句话

`audit-evidence-r*.md`（dispatch/审计回显，销毁可重生）以受治理文件身份写进 folder 并入库——24 份 / 161KB 占用治理仓，且该类文件高写入频率放大半落暴露面（`audit-evidence-r15.md` = 半落两件产物之一）。SOP `wf-77510c/sop-mcp-write.md` 明确：高频/大体积/可重生产物 → runtime root 不进仓、仓里留引用（hash+结论）。runtime root `.katana/runtime/` 已存在且 `.gitignore` 已忽略，但**无面向 folder 的产物落点 MCP tool** → worker 只能写 folder（入库）。修复 = 新增产物写入口（落 runtime root、不经 git 事务、不进仓）+ 仓内留引用收口规约 + 既存 24 份移交判定。

## 1. 现役基线（2026-08-18 真机，命令级）

- `find <work-folder-root> -name 'audit-evidence*.md' | wc -l` → **24**；`… -printf '%s\n' | awk '{s+=$1}END{print s}'` → **161672**（wf-8ba0a6 15 份 / wf-23add3 8 份 / wf-d726aa 1 份）。
- `<work-folder-root>/.gitignore` → 已含 `/.katana/runtime/`（runtime root 已忽略；runtime 内 359MB mutations.sqlite 未进仓，佐证 runtime root 不进 git 事务）。
- SOP `wf-77510c/sop-mcp-write.md`「产物往哪放」表：高频/大体积/可重生产物 → runtime root 不进仓，仓里留引用（hash+结论）。
- commit-msg hook 提示语同判据。

## 2. 判据先冻结（dd 实现前需按此定稿移交清单）

- **进 runtime root、不进仓的类别**：`audit-evidence-*.md`（dispatch/审计回显，销毁可重生）；其它「高频/大体积/可重生」产物（evidence/**__pycache__**/trace 类，按 SOP wf-77510c 表核）。
- **保留在仓的类别**：`goal.md`/`_brief.md`/`progress.md`/`evolution-backlog.md`/spec/登记/台账这类「治理事实、需版本化」的文档。
- **既存 24 份处置**：`fs_rename`（或等价）从 folder 移入 `.katana/runtime/evidence/<wf-id>/`，仓内留引用文件（含 `sha256sum` + 一行为何重生/结论指针），一次性清扫 + 落 commit；不可直接删（避免破坏审计链，保留 runtime 原文 + 仓内指针）。
- **新产物写入口**：新增 MCP tool（建议名 `wf_evidence_put` / `fs_put_artifact`，dd 定名），落 `.katana/runtime/evidence/<wf-id>/` 或等价 runtime root，不经 git 事务、不进仓，幂等；在 folder 内自动留引用文件（hash+结论）。

## 3. 变更契约（实现由 dd 落，此处冻结意图与边界）

- **`mcp/work-folder/katana_work_folder_mcp/fs_tools.py` + `server.py`**：新增产物写入口 tool + `fs_capabilities` 回显；落点 `.katana/runtime/evidence/<wf-id>/`（runtime root），不经 governed git 事务（走 runtime state 写，参照 manifest/ledger 同门 runtime 路径）。
- 引用文件收口规约：folder 内留 `audit-evidence-<t>.ref`（或 dd 定名），含 `sha256:<hex>` 指针 + 一行结论 + 原文 runtime 相对路径；体积从 161KB 收敛到指针级。
- **不改 kernel**；不改 scope/reconcile/ledger schema；不改 manifest 语义。
- 生产数据仓零直写：既存 24 份移交走 MCP governed 工具（本卷执行）或由 dd 实现提供工具后本卷执行，**不用 porcelain 绕道写**。

## 4. 三服务恢复预案（同 EK-1+EK-2 模板，命令级）

> runtime root 写入口若走 kernel runtime state 路径，波及三服务仅「新增 tool 注册」，无 kernel 事务可靠性改动；回滚预案同 EK-1+EK-2：`systemctl --user restart katana-{work-folder,memory,wiki}-mcp` + 逐服务最小探针 + 运行 commit 自证回基线 e3b6ac5。**只重启本卷测试实例，不重启他线在跑泵。**

## 5. 冻结的机器可验收命令（逐字收录自 backlog EK-4）

1. 观察窗内（`since <t0>`）：`find <work-folder-root> -name 'audit-evidence*.md' -newermt "<t0>" | wc -l` → **0**（无新增进仓），同时 `.katana/runtime/evidence/` 对应路径产物计数递增。
2. `git log --oneline --since <t1> -- '**/audit-evidence*'` → 空（无该类别新 commit）。
3. 引用完整性：folder 内引用文件可 `sha256sum` 复算出 runtime 产物 hash（仓留指针不留体积）。
4. 全仓 `bash mcp/run-tests.sh` → EXIT=0（含 dd 新增落点回归测试）。

## 6. 实现者最小交付集（冻结，供 dd reviewer 对照）

1. `mcp/work-folder/katana_work_folder_mcp/fs_tools.py` + `server.py`：新增产物写入口 tool + `fs_capabilities` 回显 + 引用文件收口。
2. 既存 24 份移交判定 + 一次性清扫工具（或提供 tool 后由本卷执行）。移交动作归属 Ops（本卷自执行），**代码实现不代跑生产移交**。
3. 回归测试：`mcp/work-folder/tests/`（产物落点不进 git 事务 + 引用 hash 复算）。
4. 三服务回滚预案随附。

## 7. 宪法检查（loop-engine constitution 逐条）

- **Article I（Admission completeness）**：验收 `bash mcp/run-tests.sh` 自足跑通——满足。
- **Article II（Total progress）**：audit-evidence 高写入频率放大半落暴露面 → 收敛到 runtime root 缩小半落窗口——满足（方向 2×3）。
- **Article IV（Integrity fail-closed）**：不移/不删原文审计链，仓内留指针 + runtime 原文——满足。
- **Article V（Test-reality parity）**：落点回归测试真实触发 runtime root 写，非伪造——满足。

## 8. 状态

🔼 spec 已冻结（2026-08-18，wf-caaeb6）。digest 由派发轮复算落档 spec-digest-ledger；dev 分支 `dd/katana/evidence-runtime`；发单顺序在 EK-3 之后。