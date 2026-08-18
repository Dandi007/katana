# SPEC EK-1+EK-2 —— kernel 治理事务可靠性整修：半落死锁消除 + reconcile 受控恢复出口（合并单，spec 冻结稿）

> 判据来源：`wf-caaeb6/evolution-backlog.md` EK-1（并发/中断致「半落」fail-stop 无自愈）+ EK-2（工具面无 reconcile 出口）。
> 交付仓：`Dandi007/katana`，**MR base 只许 `fix/governed-dirty-paths-diagnostic`@e3b6ac52c285e9c83b3f08c34cbe4c8f40fdb1a3，禁 main / 禁 katana-cutover 任何分支**（EK-0 已钉死，违反即静默丢掉已部署的 scope 机制）。
> 派单登记：`wf-caaeb6/dispatch-registry.md` §0/§1；dev 分支 `dd/katana/reconcile-atomicity`。
> 合单理由：EK-1/EK-2 同触 `mcp/kernel/katana_kernel/{gitops,kernel}.py`（单文件 ~1000 行），分两台必对撞；backlog §派发顺序已裁合一台。
> B 类候选（新增 `wf_reconcile` MCP tool 面 = 共享契约变更）：board:dd-talk 预告由泵侧在发单时代发（本 spec 冻结为预告落档物）。
> 本 spec 由 coordinator 编写（wf-caaeb6 线，ronin-wfmcp）；**实现与 review 全部由 loop-engine dev-dispatch 完成**（用户铁律：worker 侧不写生产码）。

---

## 0. 一句话

governed mutation 是「先写工作树 → 后 commit → CAS 发布 ref → 事后同步真实共享 index」，写与提交之间无原子保护：任一并发 CAS 失手或中断，败者文件已落盘未提交、`journal.rollback()` 判 BROKEN、下一笔 `require_clean_working_tree` fail-stop 锁死全卷；且工具面只暴露文件 CRUD、无 reconcile 出口，唯一恢复 = 人工 `git commit`（hook warn 放行，恰是绕道写入口）。修复 = ①`_commit_exact` 失败原子化（败者明确 `BASE_COMMIT_CONFLICT` 且不留脏）+ ②`reconcile` 从「只校验」升级为「可执行安全恢复清单」+ 工具面新增 `wf_reconcile`。

## 1. 现役基线（2026-08-18 真机，行号 = 运行中 venv `venv-wf-flat-e3b6ac5-r1` 副本）

写入链（`mcp/kernel/katana_kernel/kernel.py` `_mutate_locked` L603 起）：
1. **L664-676**：`base_sha = require_clean_working_tree(...)` —— fail-stop 先于任何写。
2. **L698**：`result = write_fn(binding=binding, args=args)` —— 字节直接写进**真实工作树**（VFS）。
3. **L802**：`git_result = git_commit(binding.repo_root, commit_message, all_paths, expected_images=..., expected_base_sha=base_sha)` —— 提交后于写，写与提交之间无原子保护。
4. **L812-828**：`if not git_result.get("committed"): rollback = journal.rollback(); ...` 若 rollback 判 BROKEN → `MutationBrokenError("governed commit failed; manual recovery required")`，**脏物保留**。

提交链（`mcp/kernel/katana_kernel/gitops.py` `_commit_exact`）：
5. `write-tree` → `commit-tree`（L ~960-996，已产出 commit 对象）。
6. **L ~1004-1011** ref 发布是 CAS：`git update-ref <ref> <commit_sha> <old_value=base_sha or 0*40>`。
7. **L ~1012-1017**：`if publish.returncode != 0: return {"committed": False, "detail": publish.stderr...}` —— 并发同 base 时**胜者推进 HEAD，败者报 `cannot lock ref ... is at X but expected Y`**，败者文件已落盘、未提交。
8. **L ~1020-1049**：提交成功后用**真实共享 index**（无 `GIT_INDEX_FILE` 隔离）做 `git update-index --add/--force-remove` 同步循环 —— 跨写者并发即争 `.git/index.lock`。

reconcile 现状（同文件 `reconcile`/`_reconcile_pending`）：
9. **`reconcile` L192-220**：仅 `require_clean_working_tree`（校验）+ `_reconcile_runtime_ledger`，**不清脏**；且只在启动 `configure()` 时调一次（`mcp/work-folder/katana_work_folder_mcp/server.py` L383），启动后无任何途径再触发。
10. **`_reconcile_pending` L400-477**：有 receipt 但树脏 → `mark_broken` + raise（无「恢复已落盘字节 → 补提交」路径）；PENDING 且树干净 base 未动 → `mark_aborted`；其余 → `mark_broken`。
11. 工具面 `fs_capabilities` 仅文件 CRUD，无 reconcile tool。半落后唯一出口 = 监督面手工 `git commit`（2026-08-18 12:2x commit `9d450363` 事故先例，hook warn 放行）。

最小复现已跑通（ephemeral 副本仓 `/tmp/kat-repro-a`，内核原语级）：两线程同 base 直调 `git_commit` → writer1 `committed=False` + `cannot lock ref ... is at 83c1337... but expected 47ec90a...`，writer2 成功；终态 `require_clean_working_tree` 抛 `DirtyWorkTreeError`，`git status --short` 留 `?? folder-1/`。

## 2. 变更契约（实现由 dd 落，此处冻结意图与边界）

### 2.1 EK-1：失败原子化（`gitops.py` + `kernel.py`）

- **CAS 失手不留脏**：`_commit_exact` 的 ref 发布失败路径（L ~1012-1017）改返回**带 `retryable=True` 与明确 code** 的失败（`BASE_COMMIT_CONFLICT`），不再混入 BROKEN；且 `_mutate_locked` 的 `if not git_result.get("committed")`（L 812-828）分支在 rollback 能清脏时（HEAD 未变、路径字节可回滚）**回滚文件字节并返回可重试的 `BASE_COMMIT_CONFLICT`**，仅在「回滚也失败」（HEAD 已变 / 路径与 receipt postimage 不等）时才保留 BROKEN + 结构化诊断。
- **锁覆盖：失败原子化边界内的写必须可逆**：`TransactionJournal` 已捕获 pre-image（`journal.capture_path`），rollback 恢复工作树字节；修复要点是让「CAS 失手」这条路径也走 rollback→clean，而非现在的「直接 BROKEN 留脏」。
- **真实共享 index 同步隔离**：提交后的 `git update-index` 同步循环（L 1020-1049）改用**临时 `GIT_INDEX_FILE`**（或等价不争抢 `.git/index.lock` 的机制），消除「并发 governed 事务竞争 git index 锁」的二次竞态源；同步语义不变（事务条目 + 非 allowlist 的 staged 条目保留不动）。
- **败者语义对外可见**：新增可区分错误类型（建议 `BaseCommitConflictError`，携 `retryable=True` + 胜者 head sha），供上层（work-folder/memory/wiki 三服务 `_mutation_error`）映射为可重试错误码（EK-3 联动，本单**只定义**内核异常类型，不落 work-folder 面映射）。

### 2.2 EK-2：reconcile 受控恢复出口（`kernel.py` + `gitops.py` 恢复原语 + `server.py` 暴露 tool）

`reconcile` 从「只校验」升级为「执行安全恢复清单」，并按 backlog 枚举的 6 类状态给出可判定动作（本段 = 设计输入冻结，dd 按此实现）：

1. **untracked-not-ignored under scope**（`git ls-files --others --exclude-standard` 命中且非 runtime 允许路径）→ 安全自动处理：产物类别（EK-4 判据）移送 runtime root 留指针，否则 commit 或确认后丢弃。
2. **tracked-modified content == 某 receipt postimage**（`git diff` 字节级 == `commit_file_image(prepared)`）→ 安全：resume 该 commit（同一 base 无冲突时直提交）。
3. **index-only staged**（`git diff --cached` 有 & 工作树同 image）→ 安全：`git reset` / finalize。
4. **ledger PREPARED + 有 receipt commit + 树干净** → 现有 `_reconcile_pending` L413-437 已支持 finalize，缺「工具面可触发」。
5. **orphan `.git/index.lock`**（持有 PID 已死）→ 安全：删除后重入。
6. **不可自动恢复**（commit 已发布但树脏且内容 ≠ 任何 receipt postimage；ref 被并发推进而 journal 有未落盘变更；porcelain 无法归到单一已知道具）→ 保 BROKEN，**但输出须含结构化诊断 + 可复制恢复指引**（mutation_id/路径/建议命令），当前只给一行 `DirtyWorkTreeError`。

- **工具面新增 `wf_reconcile`**：`mcp/work-folder/katana_work_folder_mcp/server.py` 暴露，受 MCP 治理、幂等（同 idempotency_key 幂等）、B 类。返回 `recovered:[...]` / `BROKEN:{结构化诊断}`，**类型 6 不动树**。`fs_capabilities` 回显含 `wf_reconcile`。姊妹服务（memory/wiki）同享 kernel，`reconcile` 内核逻辑一处实现三服务复用。
- **启动路径不变**：`reconcile` 仍在传入 scope 时被 gate 使用，但**新增入参仅加可 recover 行为，不改 `verify` 输出语义**（`head`/`unresolved` 键保留）。

### 2.3 非目标 / 明确不碰

- 不改 `wf_reconcile` 之外的既有 MCP tool 对外契约与返回结构；不改 scope/control 诊断机制本身（那是 e3b6ac5 部署态，**必须保留**）；不改 manifest/ledger schema；不改 commit-msg hook。
- 生产数据仓 `<work-folder-root>` 零触碰；复现与验收全在 ephemeral 副本仓 / 候选工作区 / 仓内 pytest。

## 3. 三服务恢复预案（systemd --user 命令级；deploy 单必附，本 spec 预制模板）

> kernel 改动波及三服务（work-folder / memory / wiki 共享 `mcp/kernel/katana_kernel`），部署顺序 kernel → 三服务逐个验证；失败即回滚。

```bash
# katana 三服务部署失败回滚预案（systemd --user，逐字可执行）
BASE=/data/code/releases/katana-runtime/e3b6ac52c285e9c83b3f08c34cbe4c8f40fdb1a3   # 部署前基线 release 目录
SVC=(katana-work-folder-mcp katana-memory-mcp katana-wiki-mcp)

# 0) 取证：记录回滚前状态
for s in "${SVC[@]}"; do systemctl --user is-active "$s"; done
readlink -f /data/code/releases/katana-runtime/current 2>/dev/null   # current 指针现状

# 1) 回滚：把 current 指针指回基线 release，重载并三服务逐一重启
systemctl --user daemon-reload
for s in "${SVC[@]}"; do systemctl --user restart "$s"; done
sleep 3
for s in "${SVC[@]}"; do systemctl --user is-active "$s"; done

# 2) 健康门：三服务最小探针（work-folder 为例的单卷 fs_read）
#    （具体探针按各服务 MCP 入口，只读、免 auth、不直写数据仓）

# 3) 运行 commit 自证：journal 首行 / ExecStart 路径与基线 e3b6ac5 对齐
```

> 触发回滚条件（deploy 单内约定）：① 三服务任一 `is-active` 非 active；② 部署后首探针（单卷 fs_read/edit）异常；③ kernel 契约测试（仓内 pytest）在部署态复跑变红。**只重启本卷测试实例，不重启他线在跑泵。**

## 4. 冻结的机器可验收命令（逐字收录自 backlog EK-1/EK-2）

> 自足口径（R13 假阴性四条教训）：候选工作区根 cwd / 仅 PATH+HOME / 禁假设预置 .venv 或 cd 主仓 / 回滚预案为随附文档不在仓内落生产码。

1. **全仓测试套件**：`bash mcp/run-tests.sh`（candidate 根 cwd，PYTHON 默认 `python3`，经 `--import-mode=importlib` 跑 shared/wiki/work-folder/memory/migration/kernel/remote 七包）→ **EXIT=0**，含 dd 实现新增的回归测试（见 §5）。判据逐字不变；具体通过数由派发当刻候选工作区实测。kernel 契约测试是宪法守卫，不得降级。

2. **半落负例守护（回归测试，dd 必须新增到 `mcp/kernel/tests/`）**：构造两并发同 base 写 → 修复后终态 `git status --porcelain` **为空**、败者返回可重试 `BASE_COMMIT_CONFLICT`（非 BROKEN）、胜者 commit 成功。测试名称/断言由 dd 命，但**必须覆盖 CAS 失手清脏 + 共享 index 同步不再争 `.git/index.lock`** 两条根因。

3. **reconcile 恢复正例/负例（dd 必须新增到 `mcp/kernel/tests/` + `mcp/work-folder/tests/`）**：副本仓制造类型 1/2/3/4/5 脏 → `wf_reconcile` 返回 `recovered:[...]` 且 `git status --porcelain` 空、ledger 无 PENDING/PREPARED；制造类型 6 → 返回 `BROKEN` + 结构化诊断（mutation_id/路径/建议命令）且**不改树**；`fs_capabilities` 回显含 `wf_reconcile`。

## 5. 实现者最小交付集（冻结，供 dd reviewer 对照）

1. `mcp/kernel/katana_kernel/gitops.py`：CAS 失手返回可重试 `BASE_COMMIT_CONFLICT`；提交后 index 同步改临时 `GIT_INDEX_FILE` 隔离；新增恢复原语（receipt postimage 比对 / `index.lock` 检查）。
2. `mcp/kernel/katana_kernel/kernel.py`：`_mutate_locked` 失败路径把「已写未提交文件」归类为「可回滚→BASE_COMMIT_CONFLICT」或「BROKEN+结构化诊断」；`reconcile` 增安全恢复清单执行（类型 1-6），`verify` 输出键不变。
3. `mcp/work-folder/katana_work_folder_mcp/server.py`：新增 `wf_reconcile` tool（受治理/幂等/B 类）+ `fs_capabilities` 回显。
4. 回归测试：`mcp/kernel/tests/`（半落负例 + reconcile 正/负例）+ `mcp/work-folder/tests/`（wf_reconcile 暴露）。
5. 三服务回滚预案随附文档（本 spec §3 模板的 command-level 版本）——**不落仓内生产码**。

## 6. 宪法检查（loop-engine constitution 逐条）

- **Article I（Admission completeness）**：验收命令 `bash mcp/run-tests.sh` 全仓自足跑通，无 cd 主仓、无假设 .venv、无假阴性（R13 四项硬线对齐）——满足。
- **Article II（Total progress）**：半落 BROKEN-only 是「无状态推进的自锁」→ 本单新增可重试 `BASE_COMMIT_CONFLICT` 与 6 类 reconcile 出口，为每个可安全恢复状态提供自动 exit——满足（修的就是这条欠账）。
- **Article IV（Integrity fail-closed）**：类型 6 不自动清脏、保留 BROKEN + 结构化诊断；CAS 原语不动——满足。
- **Article V（Test-reality parity）**：并发同 base 已在 ephemeral 副本仓复现，回归测试必须复断根因（非伪造）——满足。
- **Article VI（Actor contracts）**：实现者写 `mcp/kernel/**` 与 `mcp/work-folder/server.py`，验收路径文件不落在拆给 actor 的必须修改集内；reviewer 用仓内 pytest 取证——满足。

## 7. 状态

🔼 spec 已冻结（2026-08-18，wf-caaeb6 ronins-wfmcp）。EK-1+EK-2 合一台；base = `fix/governed-dirty-paths-diagnostic`@`e3b6ac52c285e9c83b3f08c34cbe4c8f40fdb1a3`（EK-0 钉死）；dev 分支 `dd/katana/reconcile-atomicity`。发单待派发轮：先核 katana 仓同名单/同 spec_revision_id 无冲突 + 构造 H0 worktree + 开 durable MR + `development_create(initial_handoff)` + board:dd-talk 预告。**spec digest 落档见 wf-caaeb6 dispatch-registry / progress。**