# Evidence Runtime 三服务回滚预案（EK-4 随附）

`wf_evidence_put` / `wf_evidence_migrate` 新增的产物落点在
`<work-folder-root>/.katana/runtime/evidence/<folder_id>/<filename>`，走
kernel 的 runtime state 路径，不经 governed git 事务、不进仓。该落点只新增了
tool 注册与一条 runtime-state allowance，未改动 kernel 事务可靠性、
scope/reconcile/ledger schema 或 manifest 语义，因此回滚只需重启三服务并做
最小探针。

## 0. 范围

- 只重启**本卷测试实例**；不重启他线在跑的泵。
- 本预案覆盖 `katana-{work-folder,memory,wiki}-mcp` 三个 systemd user 服务。

## 1. 重启命令（命令级）

```bash
systemctl --user restart katana-work-folder-mcp
systemctl --user restart katana-memory-mcp
systemctl --user restart katana-wiki-mcp
```

## 2. 逐服务最小探针

每个服务重启后，先确认进程健康，再做一条读探针确认数据面可用：

```bash
systemctl --user is-active katana-work-folder-mcp
systemctl --user is-active katana-memory-mcp
systemctl --user is-active katana-wiki-mcp
```

- work-folder：调 `wf_list`（或 `fs_list <folder_id>`）确认返回 opaque
  `folder_id`；再 `fs_read(<folder_id>, "progress.md")`。
- memory：做一次 search/lookup 读探针。
- wiki：做一次 page query/read 读探针。

任何服务启动失败，应先看 `journalctl --user -u katana-*-mcp -n 200` 保留现场。

## 3. 运行 commit 自证回基线

以"运行 commit 自证回基线"回退到冻结节点：

```bash
git -C <katana-code-checkout> rev-parse HEAD
git -C <katana-code-checkout> log --oneline -1
git -C <katana-code-checkout> show --stat e3b6ac5 --oneline
```

回基线基线为 `e3b6ac52c285e9c83b3f08c34cbe4c8f40fdb1a3`
（`fix/governed-dirty-paths-diagnostic`）。需要回退二进制时按组织的部署流程
恢复该 commit 对应的服务二进制，再重跑 §1、§2。

## 4. 回滚判定

- 若重启/探针失败或 `systemctl` 报 runtime 目录校验错误，先确认
  `.katana/runtime/` 仍在 `.gitignore` 内且未被动进 git。governed kernel 对
  runtime 目录 fail-closed；任何"runtime 目录变脏"报错优先检查这里。
- 回滚不删除 `.katana/runtime/evidence/` 下的产物（审计链原文保留）；仓内仅
  剩 `evidence/<filename>.sha256` 指针，`sha256sum` 复算验证见 EK-4 §5.3。

## 5. 引用

- `mcp/kernel/katana_kernel/kernel.py` — `_runtime_state_allowances` /
  `reconcile` runtime-state guard。
- `mcp/work-folder/katana_work_folder_mcp/server.py:configure()` — 注册
  evidence runtime-state allowance。
- `mcp/work-folder/katana_work_folder_mcp/fs_tools.py` — `wf_evidence_put` /
  `wf_evidence_migrate` 落点与引用收口。