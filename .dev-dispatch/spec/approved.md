# dev_katana_kernel_guard_scope_01 — governed mutation 守卫按 scope 收窄（消除多 client 互锁）

status: approved
date: 2026-08-13
upstream: 监督线（用户拍板 2026-08-13：「可共用同一仓库，但绝对不要互相阻塞，这是最基本的」）；实施以本文为准

## 1. 背景（真机实锤，两处独立复现）

katana kernel 的 governed mutation 在写前要求**整仓** clean：
`kernel.py:631 require_clean_working_tree(binding.repo_root, ...)`——仓里**任何**
tracked/staged/untracked 改动都会让**一切** governed mutation 被拒
（`governed mutation rejected: repository has tracked, staged, or untracked changes`）。

后果（2026-08-13 双泵实测）：work-records 仓同时承载多个 work folder（wf-87e23d 与
wf-3c3dba 两条 goal-driven 线），A 线 worker 直写盘尚未提交时，B 线（以及任何第三方
session）的 `wf_save`/`fs_write`/`wf_append_progress` 全部失败——**两条互不相干的
工作线经共享仓互锁**。同类先例：memory 域 access-log 自锁（`test_memory_server.py:180`、
PR #114 fix/memory-access-log-self-lock），说明该全仓守卫已多次造成跨作用域误伤。

## 2. 变更契约

给 kernel 的 governed mutation 引入**作用域化清洁检查**（scope-aware clean guard）：

1. **kernel API**：`GovernedKernel.mutate`（及同用该守卫的读路径，如 `kernel.py:177`）
   新增可选参数 `scope_prefixes: list[str] | None`（repo-root 相对前缀）。
   - `None`（默认）= 现行为，整仓 clean 检查，**完全向后兼容**；
   - 非空 = 清洁检查只覆盖：`scope_prefixes` 之下的路径 + 本次事务必然触碰的治理面
     （mutation ledger、manifest/INDEX 等由 binding 声明的 control paths）。
     作用域**之外**的 dirty 条目不阻塞本次 mutation。
2. **提交隔离（硬性）**：mutation 的 git commit 只允许包含事务 journal 声明的路径
   （现有 `validate_transaction_paths` / `changed_paths` 校验保持）；作用域外的
   dirty 内容**绝不可被顺手 add/commit/stash/checkout**——不属于本事务的现场一个
   字节不碰。rollback 同样只回滚 journal 路径（现行为已是，回归测试固化）。
3. **base_sha/CAS 语义不变**：`base_sha` 仍取 repo HEAD；作用域外 dirt 不影响 CAS。
4. **work-folder domain 采用**：work-folder MCP server 对 folder 级操作
   （`wf_save`/`wf_append_progress`/`fs_write`/`fs_edit`/`fs_create` 等）传
   `scope_prefixes=[<folder 目录>]`（+该 server 声明的顶层 INDEX/brief 等 control
   paths）。跨 folder 的生命周期操作（如 `wf_reindex`）可自行选择整仓语义。
5. **memory / wiki domain 本单不改**（保持默认整仓语义），仅保证 kernel 参数对其
   透明兼容；后续单独评估采用。

## 3. 非目标

- 不改 mutation ledger / journal / VFS 事务机制本身
- 不做多 client 并发写同一 folder 的锁（另议；本单只解「异 folder 互锁」）
- 不改 MCP 工具的对外 schema（纯 server 内部行为变更）

## 4. 验收标准

`bash mcp/run-tests.sh` 全绿，其中新增/扩展测试覆盖：

- [ ] 仓内存在 folder-B 的 tracked 改动 + untracked 新文件时，folder-A 的 governed
      mutation（经 `scope_prefixes=[folder-A]`）**成功**，且产生的 commit diff 中
      **不含任何 folder-B 路径**；folder-B 的 dirty 现场在 mutation 前后逐字节不变
- [ ] folder-A 自身 dirty 时，folder-A 的 mutation 仍被拒（守卫语义保留，只是收窄）
- [ ] `scope_prefixes=None` 路径行为与现行为完全一致（回归：任意 dirt 均拒）
- [ ] control paths（ledger/INDEX 等）dirty 时，即使不在 folder 前缀下也拒
      （治理面不受作用域豁免）
- [ ] work-folder server 的 folder 级工具实际传入 scope（集成用例：模拟姊妹 folder
      dirty，`wf_append_progress` 成功）
- [ ] 既有全部测试保持绿

## 5. 参考

- 守卫现场：`mcp/kernel/katana_kernel/kernel.py:629-635`、`gitops.py:399`（`require_clean_working_tree`）、`gitops.py:104`
- 互锁实证：wf-3c3dba questions.md Q2（20:05 干净窗口成功 / 20:25 姊妹卷脏 → `OPERATION_FAILED`，双向复现表）
- 同类先例：`mcp/memory/tests/test_memory_server.py:180`、PR #114（fix/memory-access-log-self-lock）
- 用户拍板原话：「可以共用同一个仓库，但绝对不要互相阻塞，这是最基本的」（2026-08-13）
