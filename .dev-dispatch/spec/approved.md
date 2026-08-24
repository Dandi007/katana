# Spec: governed mutation 崩溃原子性 —— journal 崩溃恢复路径

> spec_revision_id: `wf199d1d-journal-resume-r1`（2026-08-24 冻结）
> 目标仓：`Dandi007/katana`，base=`release/katana-infra`，触面：`mcp/kernel/katana_kernel/`（kernel.py、gitops.py、ledger.py、idempotency.py）+ `mcp/work-folder/katana_work_folder_mcp/`（server.py 的 wf_reconcile / wf_resume 恢复清单接入）。

## 1. 问题陈述（源码锚点）

`katana_kernel/kernel.py::GovernedKernel._mutate_locked` 的事务链：

1. `require_clean_working_tree`（gitops.py:462）—— 脏则拒绝一切 mutation；
2. `TransactionJournal`（gitops.py:714）—— **纯内存结构**（`_preimages`/`_expected` 只在进程内）；
3. `write_fn` 写文件 → 异常路径 `journal.rollback()`（存在且工作正常）；
4. `git_commit` 提交。

**缺陷**：步骤 3 完成 → 步骤 4 完成之间的窗口内进程死亡（SIGKILL/SIGTERM/OOM/MCP 服务重启），无任何异常可捕，journal 随进程蒸发，repo 留脏。此后步骤 1 对所有后续 mutation 返回 WORKTREE_DIRTY —— 系统无自愈路径。2026-08-24 wf-287e81 事故实证（work-folder 域 append_progress，14:03:28 留脏，楔死约 3.5h、泵 10+ 代空转）。三域（work-folder/wiki/memory）共享本 kernel，同类风险全覆盖。

## 2. 目标（机器可判不变量）

任一 governed mutation 在任意点崩溃后，存在一条 governed 恢复路径，把该事务推进到「已提交（含 receipt）」或「已回滚（干净）」二态之一；**「脏且无人认领」不得成为永久态**。

## 3. 设计决策点（实现者须遵守）

### 3.1 恢复材料持久化

- mutation 进入 `write_fn` **之前**，把恢复记录持久化：`base_sha`、touched paths、各 path 的 preimage（内容或内容哈希+取回方式）、关联 idempotency claim（若有）。
- 存放位置**优先并入现有 SQLite mutation ledger**（与 claim 同一存储、同事务语义）；**不得新增 git 跟踪文件**（恢复后 repo 必须 clean）。
- mutation 正常完成后，恢复记录标记 closed（可保留供审计）。

### 3.2 恢复入口与分类处置

在 `require_clean_working_tree` 发现脏时（以及 work-folder 的 `wf_reconcile` / `wf_resume` 恢复清单调用时），先查未关闭恢复记录：

- **a) resume**：HEAD == base_sha 且脏路径 ⊆ 记录 touched paths，且工作区内容与记录的 post-state 一致（写已完成）→ 续走 commit+finalize。**幂等**：复用原 claim，不得重复提交、不得重复副作用（重复恢复 = 返回原 receipt）。
- **b) discard**：工作区与 pre-state 一致（写未发生或已回滚）→ 弃置记录。
- **c) rollback**：HEAD == base_sha、脏路径 ⊆ touched paths、内容处于混合态 → 按记录 preimages 恢复到 pre-state，落 receipt。
- **d) fail-closed**：HEAD 已移动、脏路径超集、或任何无法判定的形态 → 保持 BROKEN 并 surface，**不得猜测动树**。

### 3.3 接入面

- `katana_kernel`：上述机制本体（kernel.py 调用点 + gitops.py/ledger.py/idempotency.py 支撑）。
- `katana_work_folder_mcp`：`wf_reconcile` / `wf_resume` 的恢复清单纳入 3.2 路径（work-folder 域是事故首发面，须先行可验证）。
- wiki/memory 两域经 kernel 自动受益，不要求本单改它们的 server 代码。

### 3.4 非目标

- 不改 mutation 正常路径语义（异常回滚、CAS、policy verify 原样）。
- 不改变对「非 journal 残留」的 WORKTREE_DIRTY 拒绝行为。
- 不引入新的 git 跟踪文件；kernel 保持 stdlib only（现 `dependencies = []`）。
- 不做部署（新 katana release 投放属后续 B 类，不在本单）。

## 4. 冻结验收命令

1. `bash mcp/run-tests.sh` 全绿（PYTHON 指向带 pytest 的 python3；含**新增**崩溃恢复测试，至少覆盖）：
   - 崩溃注入：write_fn 成功后、git_commit 前制造进程级死亡（或等效异常注入使 rollback 不执行）→ 触发恢复路径 → repo 终态 ∈ {committed+clean, reverted-clean}；重复触发恢复 = 幂等（无二次提交，返回原 receipt）；
   - HEAD 已移动场景 → fail-closed（BROKEN，不动树）；
   - 无恢复记录的脏 → 仍 WORKTREE_DIRTY 拒绝；
   - 正常 mutation 路径回归全绿（存量测试不许删改语义）。
2. 恢复路径执行后 `git status --porcelain` 输出为空（恢复材料不落 git 跟踪文件）。
3. `grep -c "class TransactionJournal" mcp/kernel/katana_kernel/gitops.py` ≥ 1（journal 结构仍在，演进而非替换）。

## 5. 恢复 receipt 最小 schema

每次恢复动作（resume/discard/rollback/fail-closed）落一条结构化 receipt：actor、timestamp、action、关联 claim/mutation_id（若有）、base_sha、touched paths、终态（commit sha 或 clean 证明）、判定依据（3.2 的 a/b/c/d 分类）。
