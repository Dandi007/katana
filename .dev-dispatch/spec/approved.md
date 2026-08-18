# SPEC EK-3 —— 错误上抛一致化 + 应用层结构化日志（spec 冻结稿）

> 判据来源：`wf-caaeb6/evolution-backlog.md` EK-3（fs_* 吞 DirtyWorkTreeError 成 OPERATION_FAILED；wf_append_progress 反泄漏裸字符串；应用层零 logging）。
> 交付仓：`Dandi007/katana`，**MR base 只许 `fix/governed-dirty-paths-diagnostic`@e3b6ac52c285e9c83b3f08c34cbe4c8f40fdb1a3，禁 main / 禁 katana-cutover 任何分支**（EK-0 钉死）。
> 派单登记：`wf-caaeb6/dispatch-registry.md` §0/§1；dev 分支 `dd/katana/error-observability`。
> 与 EK-1+EK-2 文件面**不交叠**（本单 work-folder 面 vs EK-1+EK-2 kernel 面），但同仓 base 均 = e3b6ac5 → 按本卷顺序在 EK-1+EK-2 之后发（spec 可先行冻结）。
> 本 spec 由 coordinator 编写（wf-caaeb6 线）；实现与 review 全由 dev-dispatch 完成。

---

## 0. 一句话

同一真因 `DirtyWorkTreeError`，两个工具面两种行为：`fs_*` 的 `_mutation_error` isinstance 链漏收它 → 吞成通用 `OPERATION_FAILED`；`wf_append_progress` 的 except 链也没收它 → 反把裸字符串泄漏进响应；且全 work-folder + kernel 应用层零 `logging`，journald 只有 fastmcp access log。修复 = ①`_mutation_error` 补 `DirtyWorkTreeError`→`WORKTREE_DIRTY`（`_ERROR_CODES` 增码）+ `append_progress` except 链补齐 ②`_server_mutation` 收 `DirtyWorkTreeError`/`CASRejectionError` ③新增结构化 logger（mutation_id/commit_sha/op/domain/真因落 journald），实现「事务失败可归因」北极星。

## 1. 现役基线（2026-08-18 真机，行号 = venv-wf-flat-e3b6ac5-r1 副本）

- `mcp/work-folder/katana_work_folder_mcp/fs_tools.py`：
  - `_ERROR_CODES`（L39-53）：无 `WORKTREE_DIRTY` 码。
  - `_mutation_error`（L361-425）：isinstance 链收 `IdempotencyConflictError/CASRejectionError/MutationBrokenError/PolicyError/BatchError/ValueError`，**漏 `DirtyWorkTreeError`** → 落到 L419-425 通用 `OPERATION_FAILED "operation failed; inspect server-side logs"`。
- `mcp/work-folder/katana_work_folder_mcp/store.py`：`append_progress`（L928-963）except 链（`IdempotencyConflictError/CASRejectionError/BriefError + ValueError`）**无 `DirtyWorkTreeError` 分支** → 未捕获 → 直抛给 fastmcp → 裸字符串「governed mutation rejected: repository has tracked, staged, or untracked changes within scope」泄漏进响应（12:22 journal 所见）。
- `mcp/work-folder/katana_work_folder_mcp/server.py`：`_server_mutation`（L136-147）只收 `IdempotencyConflictError`/`MutationBrokenError`；`DirtyWorkTreeError`/`CASRejectionError` 未收 → 落到 fastmcp 打印 rich traceback，不落结构化日志。
- 命令级证据：`grep -rn "import logging\|logging\." …/katana_work_folder_mcp …/katana_kernel`（排除 `audit_logger=` 参数）→ **0 命中**；`journalctl --user -u katana-work-folder-mcp --since 12:20 --until 12:27` 半落当刻仅 `INFO: … "POST /mcp HTTP/1.1" 200/202`，无 ref-publish / DirtyWorkTree 应用行。

## 2. 变更契约（实现由 dd 落，此处冻结意图与边界）

### 2.1 错误码一致化（`fs_tools.py`）
- `_ERROR_CODES` 增 `WORKTREE_DIRTY`（含 retryable 语义：半落可经 reconcile 恢复 → retryable=true）。
- `_mutation_error` isinstance 链增 `DirtyWorkTreeError`→ `WORKTREE_DIRTY`，message 含 scope/路径真因（非「inspect server-side logs」）。

### 2.2 append_progress 收口（`store.py`）
- `append_progress` except 链补 `DirtyWorkTreeError`，同因返回受控 envelope（`WORKTREE_DIRTY` + 路径/scope），**不再泄漏裸字符串**。与 fs_* 行为对齐（同一真因，同一 envelope 家族）。

### 2.3 server 面结构化日志（`server.py`）+ 可能 kernel
- `_server_mutation` 收 `DirtyWorkTreeError` / `CASRejectionError`（不落 fastmcp traceback）。
- 新增结构化 logger（`import logging`，journald 可见）：mutation_id、commit_sha、op、domain、异常真因（`DirtyWorkTreeError`/`update_ref`/error_code）。正常提交也留 `mutation_id`/`commit`（可归因闭环）。
- 不改 kernel 代码（EK-1+EK-2 内核异常类型定义联动由其对侧实现，本单只在 work-folder 面映射；若 dd 判断需 kernel 加一个可注入 logger 传参，属本单文件面外的加法，须在返工说明里落档并走 review）。

### 2.4 非目标 / 明确不碰
- 不改 kernel 事务可靠性 / reconcile / scope 机制（EK-1+EK-2 面）；不改 manifest/ledger schema；不改既有 tool 语义（只改错误 envelope 与日志面）。
- 生产数据仓零触碰；复现与验收全在副本仓 / 候选工作区。

## 3. 三服务恢复预案（同 EK-1+EK-2 模板，命令级）

> 本单触 work-folder 面，但 kernel 若被 dd 判需加 logger 传参则波及三服务；回滚预案同 EK-1+EK-2：`systemctl --user restart katana-{work-folder,memory,wiki}-mcp` + 逐服务最小探针 + 运行 commit 自证回基线 e3b6ac5。**只重启本卷测试实例，不重启他线在跑泵。**

## 4. 冻结的机器可验收命令（逐字收录自 backlog EK-3）

1. 半落副本仓上 `fs_edit` → 响应 `code=="WORKTREE_DIRTY"` 且 message 含路径/scope（**非** OPERATION_FAILED）；`wf_append_progress` 同因响应同为受控 envelope（**非**泄漏裸字符串）。
2. 触发一次 mutation 失败 → `journalctl --user -u katana-work-folder-mcp --since <t>` 命中结构化行（含 `mutation_id=<hex>` + 异常真因关键字 `DirtyWorkTreeError`/`update_ref`）。
3. 反例：正常提交也在 journald 留 `mutation_id`/`commit`（可归因闭环）。
4. 全仓 `bash mcp/run-tests.sh` → EXIT=0（含 dd 新增的错误码/日志回归测试，见 §5）。

## 5. 实现者最小交付集（冻结，供 dd reviewer 对照）

1. `mcp/work-folder/katana_work_folder_mcp/fs_tools.py`：`_ERROR_CODES` + `_mutation_error` 补 `WORKTREE_DIRTY`。
2. `mcp/work-folder/katana_work_folder_mcp/store.py`：`append_progress` except 链补收口。
3. `mcp/work-folder/katana_work_folder_mcp/server.py`：`_server_mutation` 收新异常 + 结构化 logger。
4. 回归测试：`mcp/work-folder/tests/`（错误码映射 + envelope 一致性 + journald 结构化行）。
5. （若 dd 判定需 kernel logger 传参）返工说明落档 + review，三服务回滚预案随附。

## 6. 宪法检查（loop-engine constitution 逐条）

- **Article I（Admission completeness）**：验收 `bash mcp/run-tests.sh` 自足跑通——满足。
- **Article II（Total progress）**：错误被吞成 OPERATION_FAILED 使失败不可归因 → 本单让失败显式化为可重试 `WORKTREE_DIRTY`，消除「不可归因」的隐性自锁——满足（方向 3）。
- **Article IV（Integrity fail-closed）**：不改 CAS/校验，只改错误面与日志面——满足。
- **Article V（Test-reality parity）**：journald 结构化行回归用真实触发，非伪造——满足。

## 7. 状态

🔼 spec 已冻结（2026-08-18，wf-caaeb6）。梳理 digest 由派发轮复算落档 spec-digest-ledger；dev 分支 `dd/katana/error-observability`；base 见 §头。发单顺序在 EK-1+EK-2 之后。