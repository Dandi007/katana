# Execution Spec — katana P0 检索接线：两个 MCP server 从 vault_search 改走域内 DomainSearch

## Identity and base

- Repository: `https://github.com/Dandi007/katana.git`
- Base branch: `release/katana-mcp-search-wiring`（seed = `d455747e000c42f060b8b35bfd4ed04737dd0433`）
- 语言/工具链：Python ≥ 3.11，各子包 `pyproject.toml`，测试用 pytest（`mcp/run-tests.sh` 是 gate 入口）
- 父设计（权威，不得抵触）：work folder `wf-77510c`；宪法 `docs/constitution/002-data-plane-privacy.md`
- 范围：**只做接线**。`katana_search`（`mcp/search/`）的检索算法、分块策略、融合公式、embedding 客户端熔断参数一律不动，除本 spec 交付项 1 明确列出的索引簿记缺陷之外。

## Background —— 为什么这单必须存在

`mcp/search/katana_search`（`DomainSearch`）在 commit `60a7d65` 已经落地：索引是每域自持的一个 SQLite
文件，躺在该域自己的卷里（`.katana/runtime/search/index.sqlite`，gitignored）；向量面走共享的无状态
embedding 服务（`d455747` 起在 compose 网络里，服务名 `embedding`）。

**但没有任何一个 server 调它。** 两个 server 至今仍 import 并调用宿主上的共享检索栈：

```
mcp/wiki/katana_wiki_mcp/server.py:12    from katana_kb_mcp_shared import config, vault_search
mcp/wiki/katana_wiki_mcp/server.py:94        resp = vault_search.search(query, top_k=fetch_k, dir=scope)
mcp/wiki/katana_wiki_mcp/server.py:126       resp = vault_search.search(query, top_k=fetch_k, dir=dir, **kwargs)
mcp/work-folder/katana_work_folder_mcp/server.py:19   from katana_kb_mcp_shared import config, vault_search
mcp/work-folder/katana_work_folder_mcp/server.py:414      response = vault_search.search(
```

后果有三条，每条都是硬伤：

1. **容器化后检索读路径直接不可用。** vault-search 只监听宿主 `127.0.0.1:18082`，容器经 bridge
   网关出来打不到宿主 loopback（`deploy/README.md`「待决：检索后端在容器内不可达」）。三域写路径
   全部已验证可用，唯独读路径挂在这一条上。
2. **它违反宪法 002 第一条。** 共享索引器要直接读所有域的文件系统，是「data root 是 MCP 进程私有
   成员」唯一的例外。域内自持索引就是为消掉这个例外而写的。
3. **旁路索引器会烂而无人知。** 上一个共享索引器死了 9 天没人发现——因为没有任何一个域为它负责。
   `DomainSearch` 把索引更新做成写路径的属性（post-commit 进程内调用），freshness 不再依赖一个会死
   的 watchdog。这条纪律只有真正接上钩子才成立。

本单把接线做完：读路径改走 `DomainSearch`，写路径挂上 post-commit 索引钩子，补上显式的重建入口与
首次全量回填路径，并把 `mcp/search` 的测试接进 merge gate。

## Scope

**允许改动**：

- `mcp/search/**`（含 `mcp/search/tests/**`）
- `mcp/wiki/**`（含 `mcp/wiki/tests/**`）
- `mcp/work-folder/**`（含 `mcp/work-folder/tests/**`）
- `mcp/run-tests.sh`
- `mcp/conftest.py`
- `.github/workflows/tests.yml`

**禁止改动**（改了即 REJECT）：

- `mcp/kernel/**` —— governed 事务内核是另一条线的资产，本单一行都不许碰。索引钩子必须挂在各域
  自己的 server/FSTools/store 层，**不许**下沉进 `GovernedKernel.mutate`。
- `mcp/shared/**` —— `katana_kb_mcp_shared.vault_search` 模块本身保持原样（宿主形态的其他调用方仍
  在用）。本单只是让这两个 server 不再 import 它。`mcp/shared/tests/test_smoke.py` 断言
  `__all__ == ["config", "vault_search"]`，该断言必须继续通过。
- `mcp/memory/**`、`mcp/migration/**`、`mcp/remote/**`、`mcp/wiki-v2/**`
- `deploy/**`、`docs/**`、`plugins/**`、`parity/**`、`scripts/**`
- 生产数据目录 `/data/wiki`、`/data/work-records`、`/data/memory` —— 测试一律在 `tmp_path` 里建仓。

**不在本单范围**（遇到挂 question，不代拍）：容器迁移上线、四容器切换、PR merge、任何 `systemctl`
或 `docker` 操作。本单是纯代码单，**不部署、不重启任何服务**。

---

## 交付项

### D1 —— 索引簿记：向量面缺失必须可恢复

**现状缺陷（先证明它是真的）**：`SearchIndex.upsert()` 无条件写 `docs.hash`
（`mcp/search/katana_search/index.py`）；`DomainSearch.index_document()` 在 embedding 不可用时走
`upsert(path, text, vectors=None)`，只建关键词面。于是 `needs_reindex(path, text)` 之后恒返回
`False`——**端点恢复后向量面永久缺失**，除非有人手工带 `force=True` 重来。

而首次全量回填恰恰是最可能撞上 embedding 未就绪的时刻（容器冷启、embedding 还在 `start_period`
里）。不修这一条，交付项 D2/D6 都会在真实时序下静默产出一个只有关键词面的索引，而 `stats()` 里
`vectors: 0` 没有任何人会去看。

**要求**：

1. `docs` 表增列 `vectors_complete INTEGER NOT NULL DEFAULT 0`。迁移必须对**既有库**幂等：用
   `PRAGMA table_info(docs)` 检测后再 `ALTER TABLE`，不得 drop/recreate（卷里已有的索引不能丢）。
2. `upsert()` 按本次是否真的写入了向量置位 `vectors_complete`（写了 = 1，`vectors is None` 或
   `vec_available` 为假 = 0）。
3. `needs_reindex(path, text, *, want_vectors: bool = False)`：
   - `docs` 无该 path → `True`
   - hash 不同 → `True`
   - hash 相同、`want_vectors=True` 且 `vectors_complete=0` → `True`
   - 其余 → `False`
   - `want_vectors` 默认 `False`，既有调用点行为不变。
4. `DomainSearch.index_document()` 传 `want_vectors = not self.embedder.disabled_by_env`。
   embed 抛 `EmbeddingUnavailable` 时仍照旧 `upsert(vectors=None)` 且保持 `vectors_complete=0`，
   `degraded_reason` 照旧回填——下次写到同一篇时自然重试。熔断（`embed.py` 的
   `_FAILURE_THRESHOLD`/`_COOLDOWN_SECONDS`）保证冷却期内零网络开销，**不许**为此再加一层节流。
5. `DomainSearch.index_document()` 的返回值增加 `"vectors_complete": bool`，`stats()` 增加
   `"docs_missing_vectors": int`。降级要看得见，这是本仓已立的纪律（`api.py` 模块 docstring 第 2 条）。

**先红验收**（实现前必须 FAIL）——新增到 `mcp/search/tests/test_domain_search.py`：

- `test_index_document_reindexes_when_vectors_missing_and_embedder_recovers`
  用 `DownEmbedder` 建索引 → 换 `FakeEmbedder` → 对**同样内容**再调 `index_document` →
  断言返回 `skipped` 非真、`vectors is True`、`stats()["vectors"] > 0`。
  *今日必红*：今日第二次调用返回 `{"skipped": True, "reason": "hash unchanged"}`。
- `test_migration_adds_vectors_complete_to_legacy_db`
  先手写旧三列 schema 造一个 `index.sqlite`（含一行 docs），再 `SearchIndex(root)` →
  断言 `vectors_complete` 列存在、旧行值为 0、旧行数据未丢。
  *今日必红*：列不存在，查询 `OperationalError`。
- `test_stats_reports_docs_missing_vectors`
  *今日必红*：`stats()` 无该键。

### D2 —— 首次全量回填路径

**要求**：新增 `mcp/search/katana_search/backfill.py`，可作模块跑：

```
python -m katana_search.backfill --root <repo_root> [--force] [--json]
```

- 待索引集合 = `git -C <root> ls-files -z -- '*.md'` 的结果。**必须走 git ls-files 而不是
  `Path.rglob`**：后者会扫进 `.katana/runtime/`（索引自己）、`.git/`，以及 work-folder 里
  gitignored 的运行时产物。
- 单文件超过 1 MiB 跳过并计入 `skipped_too_large`（分块+向量化一篇超大文件的代价不该由回填
  路径静默承担）。
- 逐篇调 `DomainSearch.index_document(path, text, force=force)`。
- 退出码：内容全部入库 → `0`（**即使 embedding 全程不可用**——降级不是失败，是显式状态）；
  有文件读取/解码失败 → `1`。
- `--json` 输出汇总：`{"root", "total", "indexed", "skipped", "skipped_too_large", "failed",
  "errors": [{"path","error"}], "stats": {...}, "embedding": {...}}`；不带 `--json` 输出人读摘要。
- **不得**在 `configure()` 里同步跑全量回填。理由写进代码注释：会把 MCP 启动阻塞到分钟级，且
  容器冷启时 embedding 往往还在 `start_period` 里，等于把「只有关键词面」的索引钉死（D1 修的正是
  这个失效模式的另一半）。冷启回填由 D6 的工具或本模块显式触发。
- `configure()` 允许做的只有一件事：读 `stats()`，若 `docs == 0` 而仓内有 `.md`，在启动日志里
  打一条 `WARNING`，指明「索引为空，需跑 <域>_search_reindex 或 python -m katana_search.backfill」。

**先红验收**——新增 `mcp/search/tests/test_backfill.py`：

- `test_backfill_indexes_tracked_markdown_only`
  造 tmp git 仓：两篇 tracked `.md`、一篇 untracked `.md`、一个 `.katana/runtime/search/index.sqlite`
  → 断言 `indexed == 2`，且索引里没有 untracked 那篇的 path。
- `test_backfill_is_idempotent`
  连跑两次（`FakeEmbedder`）→ 第二次 `indexed == 0` 且 `skipped == 2`。
- `test_backfill_exit_zero_when_embedding_down`
  `DownEmbedder` → 退出码 0、`indexed == 2`、`stats()["docs_missing_vectors"] == 2`。
- `test_backfill_skips_oversize_file`

*四条今日全红*：模块不存在，`ImportError`。

### D3 —— wiki server 读路径改走 DomainSearch

**要求**（`mcp/wiki/katana_wiki_mcp/server.py`）：

1. `from katana_kb_mcp_shared import config, vault_search` → `from katana_kb_mcp_shared import config`；
   新增 `from katana_search import DomainSearch`。改完模块内**零** `vault_search` 字样。
2. 模块级 `_search: DomainSearch | None = None`。`_init_kernel(wiki_root)` 在成功绑定 kernel 后建
   `_search = DomainSearch(wiki_root)`；`wiki_root` 不是目录时保持 `None`（与现有早退一致）。
3. `_do_search(query, top_k, scope)` → `_do_search(query, top_k)`，内部调 `_search.search(query, top_k)`。
   - `scope` 参数与 `compute_scope()` 的**跨域过滤用途**消失：域内索引只含 wiki 自己的文件，
     `_is_wiki_path()` 的 over-fetch + 过滤那一整套删掉。
   - `compute_scope()` 函数本身保留（其返回值另有调用方），但不再喂给检索。
   - **纵深防御保留**：返回前仍校验每条 path 在 wiki repo 内是真实文件，不是就丢弃（索引可能
     滞后于一次仓外删除）。这一条不许省。
4. **路径语义**：`wiki_search` 返回的 `path` 从「kb_root-relative」改为「wiki_root-relative」。
   生产部署里 `KATANA_KB_ROOT == KATANA_WIKI_ROOT == /data/wiki`（`deploy/docker-compose.yml`），
   两者逐字相同，**生产语义不变**。必须有一条测试把这个等价关系锁住，并有一条测试锁住
   `wiki_root != kb_root` 时新语义是 wiki_root-relative。
5. `title` 字段：`SearchOutcome` 不带 title。新增 `_title_for(path) -> str`：读该文件首个
   `^#\s+(.+)$` 行做标题；读不到/无标题 → basename 去 `.md`。结果 strip 后截断到 120 字符。
   `wiki_search` 的返回契约 `[{path, score, title, snippet}]` **逐字不变**。
6. `_wiki_scoped_search(query, *, top_k=10, dir=None, **kwargs)` 保留（`query`/`ingest` 依赖它做
   判重候选，签名是它们的注入契约），内部改走 `_search`，返回一个轻量响应对象，其 `.results`
   为带 `.path/.score/.title/.snippet` 属性的条目。`dir` 参数保留但忽略并在 docstring 里写明
   「域内索引天然只含 wiki，不再需要 dir 过滤」。
7. **降级可见**：`wiki_search` 返回类型不变（list）；把 `outcome.mode` / `outcome.embedding` /
   `outcome.degraded_reason` 写进 `logging.getLogger("katana.search")` 的结构化日志。
   `wiki_query` 返回的 dict 增加 `"search_mode"` 与 `"embedding"` 两个键（fat tool 可扩展）。

**先红验收**——新增 `mcp/wiki/tests/test_search_domain.py`：

- `test_server_module_has_no_vault_search_reference`
  读 `server.py` 源码断言不含 `vault_search`，并断言 `not hasattr(server, "vault_search")`。
- `test_wiki_search_hits_page_indexed_in_domain_index`
  tmp wiki 仓 + 假 embedder → 写一页 → 建索引 → `wiki_search` 命中该页且返回四键。
- `test_wiki_search_path_is_wiki_root_relative_when_roots_differ`
- `test_wiki_search_path_matches_kb_relative_when_roots_equal`（锁生产等价）
- `test_wiki_search_degrades_to_keyword_when_embedding_down`
- `test_wiki_query_exposes_search_mode_and_embedding`

*今日全红*：今日走的是 `vault_search.search`，tmp 仓里根本没有对应后端。

同时**改写**下列文件里所有 `monkeypatch.setattr(server.vault_search, "search", ...)` 桩，改为注入
`DomainSearch` / 假 embedder，改完全仓 `mcp/wiki/tests/` 与 `mcp/work-folder/tests/` 里**零**
`server.vault_search` 引用：
`mcp/wiki/tests/test_search.py`、`test_query.py`、`test_ingest.py`、`test_integration.py:215,262,274,288,297,320`。
`mcp/wiki/katana_wiki_mcp/ingest.py:292` 的 docstring「`search_fn`: vault_search.search 兼容签名」
同步改成描述新的注入契约。

### D4 —— work-folder server 读路径改走 DomainSearch

**要求**（`mcp/work-folder/katana_work_folder_mcp/server.py`）：

1. 去 `vault_search` import，加 `DomainSearch`；模块内零 `vault_search` 字样。
2. `configure(repo_root)` 在 `kernel.reconcile("work-folder")` **成功之后**建 `_search = DomainSearch(root)`。
   顺序不许颠倒：仓脏时 `reconcile` 会抛 `DirtyWorkTreeError`，此时不该已经在卷里建出索引文件。
3. `_do_search(query, top_k)` 调 `_search.search()`。索引里的 path 就是 repo-relative 的
   `wf-<6hex>/<filename>`，正是要拆的 locator：
   - 删掉 `source_root` / `source_id`（`hashlib.sha256(_repo_root)`）那套 backend source 过滤——
     域内索引天然只含本仓，隔离由索引边界本身给出。
   - `_SEARCH_OVERSAMPLE_FACTOR` / `_SEARCH_MIN_CANDIDATES` 的 over-fetch 仍保留（校验会丢弃条目）。
   - **保留** `_safe_repo_relative()` + `ID_RE.fullmatch(folder_id)` 双重校验（纵深防御，注释里
     那句「Backend source filter 是主隔离边界」改成「索引边界是主隔离边界」）。
   - snippet 仍过 `_redact_string()`。
4. `title`：与 D3 同规则（首个 `# ` 标题 → 回落 basename）。返回契约
   `[{folder_id, filename, score, title, snippet}]` 逐字不变。
5. 降级可见：同 D3，写 `katana.search` 日志。

**先红验收**——新增 `mcp/work-folder/tests/test_search_domain.py`：

- `test_server_module_has_no_vault_search_reference`
- `test_wf_search_hits_file_indexed_in_domain_index`
- `test_wf_search_locator_splits_into_folder_id_and_filename`
- `test_wf_search_drops_paths_outside_flat_topology`（索引里塞一条 `../etc/passwd` 与一条
  `not-a-folder/x.md`，断言都被丢弃）
- `test_wf_search_redacts_snippet`
- `test_wf_search_degrades_to_keyword_when_embedding_down`

*今日全红*。同时改写 `mcp/work-folder/tests/test_server.py:342,419,481`、`test_integration.py:197`、
`test_flat_contract.py:184` 的 `vault_search` 桩。

### D5 —— post-commit 索引钩子（本单的重心）

**要求**：每域新增 `search_hook.py`（`katana_wiki_mcp/search_hook.py`、
`katana_work_folder_mcp/search_hook.py`），暴露一个函数：

```python
def after_commit(search, repo_root: str, changes: list[tuple[str, str]]) -> None:
    """changes: [(op, repo_relative_path)]，op ∈ {"upsert", "remove"}。"""
```

挂点纪律（三条，全部照抄 `mcp/search/katana_search/api.py` 模块 docstring 已经立好的规矩，实现
不得自创第四条）：

1. **只在 governed commit 成功之后跑。** 失败路径、回滚路径、`dry_run` 路径一律不跑。
2. **best-effort。** 钩子内任何异常一律吞掉并 `logging.getLogger("katana.search").warning(...)`
   留痕，**不得**改变 tool 的返回值，**不得**让一次成功的写事务在调用方看起来像失败。内容是权威，
   索引是派生物。
3. **索引绝不进 git 事务。** 索引写在 `.katana/runtime/search/`（gitignored）。journal 会做
   declared-paths 校验，把索引写进去必然 `RollbackSafetyError`。

挂接位置——每域一个统一出口，**不许**在每个 tool 里各写一遍：

- work-folder：`FSTools._call_mutate()`（`fs_tools.py`，所有 `fs_*` 与 `fs_batch` 的唯一收口）
  返回成功之后；以及 `WorkFolderStore` 里自行调 `kernel.mutate` 的每个出口
  （`wf_create` / `wf_save` / `wf_append_progress` / `wf_reindex`）。
- wiki：`FSTools._mutate()`（`fs_tools.py:242` 一带）返回成功之后；以及 `WikiStore` /
  `ingest.apply` 的出口。

`changes` 的推导必须按 op 语义精确：`fs_delete` → `remove`；`fs_rename` → 源 `remove` + 目标
`upsert`；`fs_copy` → 目标 `upsert`；`fs_batch` → 逐条展开；非 `.md` 文件不入索引。

**实现方必须自己枚举本域所有会产生 commit 的 MCP tool 并逐个接上**——下面的红测就是为了强制这
一点，漏一个就红。

**先红验收**——新增 `mcp/wiki/tests/test_search_hook.py` 与 `mcp/work-folder/tests/test_search_hook.py`：

- `test_every_committing_tool_updates_index` —— **参数化枚举**本域每一个会 commit 的 tool，逐个
  调用后断言索引反映了改动（新增/更新的 path 能被 `search()` 命中，或 `stats()["docs"]` 相应变化）。
  枚举清单（少一个即算未完成）：
  - wiki：`fs_create` / `fs_write` / `fs_edit` / `fs_copy` / `fs_rename` / `fs_delete` /
    `fs_batch` / `wiki_ingest_apply`
  - work-folder：`fs_create` / `fs_write` / `fs_edit` / `fs_copy` / `fs_rename` / `fs_delete` /
    `fs_batch` / `wf_create` / `wf_save` / `wf_append_progress` / `wf_reindex`
- `test_delete_removes_document_from_index`
- `test_rename_moves_document_in_index`（旧 path 查不到、新 path 查得到）
- `test_index_write_leaves_repo_clean` —— 钩子跑完后 `git status --porcelain` 为空。这是防
  `RollbackSafetyError` 那一类事故的结构性断言。
- `test_index_failure_does_not_fail_the_write` —— 注入一个 `index_document` 必抛的假 search，
  断言 `fs_create` 仍返回 `ok` 且 `commit` 非空，且 warning 被记录。
- `test_hook_not_invoked_on_failed_mutation` —— 用错的 `expected_base_sha` 制造冲突，断言索引
  零变化。
- `test_hook_not_invoked_on_dry_run`（work-folder `wf_reindex(dry_run=True)`）

*今日全红*：`search_hook` 模块不存在。

### D6 —— 显式重建工具 `wiki_search_reindex` / `wf_search_reindex`

**命名是硬要求**：`mcp/work-folder/katana_work_folder_mcp/server.py:538` 已有一个 `wf_reindex`，
它重建的是**顶层 `INDEX.md`**（`reindex.py`，governed mutation，会产生 commit），与检索索引毫无
关系。**不得复用、不得改名、不得改它的语义、不得往它里面塞检索逻辑。** 新工具一律叫：

- `wiki_search_reindex`
- `wf_search_reindex`

签名与语义（两域一致）：

```python
async def <domain>_search_reindex(paths: list[str] | None = None, force: bool = False) -> dict
```

- `paths is None` → 全量，复用 D2 的 backfill 逻辑（不得复制粘贴一份）
- 否则只处理给定的 repo-relative 路径；仓内已不存在的 path → `remove_document`
- 返回 `{"indexed": int, "skipped": int, "removed": int, "failed": int,
  "errors": [{"path","error"}], "stats": {...}, "embedding": {...}}`
- **非 governed**：不走 `kernel.mutate`，不产生 commit，不需要 `expected_base_sha`/`idempotency_key`。
  索引是 runtime 态，不是受治理内容。

**先红验收**：

- `test_search_reindex_full_then_incremental`
- `test_search_reindex_removes_vanished_paths`
- `test_search_reindex_is_not_a_governed_mutation` —— 调用前后 HEAD 不变且 `git status --porcelain` 为空
- `test_search_reindex_force_rebuilds_vectors`（配合 D1）
- `test_wf_reindex_still_rebuilds_index_md_and_commits` —— **锁住旧工具语义没被动过**
- `test_both_reindex_tools_are_registered`（两个工具都在 `list_tools()` 里，且 `wf_reindex` 仍在）

### D7 —— 把 `mcp/search` 接进 merge gate

现状——三处都漏了 `search`，缺一不可：

1. `mcp/conftest.py` 的 sys.path 注入列表是
   `["kernel", "memory", "migration", "remote", "shared", "wiki", "wiki-v2", "work-folder"]`，
   **没有 `"search"`**。这才是根因：两个 server 一旦 `from katana_search import DomainSearch`，
   在不装包的跑法下会直接 `ModuleNotFoundError`，整个 gate 崩掉。
2. `mcp/run-tests.sh` 的 pytest 目标是七包，**不含 `mcp/search/tests`**。
3. `.github/workflows/tests.yml:45` 的 `uv pip install` 没有 `-e mcp/search`。

于是 `mcp/search/tests/test_domain_search.py` 至今从未在任何 gate 里跑过。

**要求**：

1. `mcp/conftest.py` 的列表加 `"search"`。**这一项优先级最高**——不做，D3/D4 改完后连
   `bash mcp/run-tests.sh` 都跑不起来。
2. `mcp/run-tests.sh` 的 pytest 目标列表加 `"$HERE/search/tests"`，注释里的「七包」同步更新。
3. `.github/workflows/tests.yml` 的安装行加 `-e mcp/search`。
4. `sqlite-vec` 装不上时 `SearchIndex._load_vec()` 会退化成 keyword-only——所有新测试必须在
   `vec_available` 为真/为假两种环境下都通过，**不得**要求跑测环境一定有 sqlite-vec。
   同理 `katana_search` 在测试路径上**不得**新增任何 import-time 硬依赖。

---

## Constraints

- 不得联网。测试**一律**注入假 embedder，不得依赖真实 embedding 端点，也不得依赖宿主 vault-search。
- 测试一律在 `tmp_path` 建仓，**不得**触碰 `/data/wiki`、`/data/work-records`、`/data/memory`。
- 每个测试至少一条无条件断言；禁止 `assert True` / `assertTrue(True)` 一类恒真断言。
- 不得靠放宽既有判据变绿。既有测试如与新契约冲突，必须改成断言**新契约**，不得删除或 skip。
- 不得引入新的第三方依赖。`katana_search` 已有 `httpx` + `sqlite-vec`，够用。
- 不得在本单里做部署、重启服务、改 compose、改 `deploy/**`。
- 单个 commit 一个聚焦关注点为宜（不 gate），最终候选必须自洽。

## Acceptance（在干净的 candidate checkout 里全部通过）

1. `bash mcp/run-tests.sh` —— 必须在**不预装任何包**的裸环境下也能跑通（靠 `mcp/conftest.py`
   的 sys.path 注入，见 D7.1），因为验收就是这么跑的。
2. 契约脚本（见 `acceptance_commands`），逐条机械校验：
   - 两个 server 模块源码里 `vault_search` 出现次数为 **0**
   - `mcp/wiki/tests/`、`mcp/work-folder/tests/` 里 `server.vault_search` 出现次数为 **0**
   - `mcp/search/katana_search/backfill.py` 存在
   - `mcp/wiki/katana_wiki_mcp/search_hook.py`、`mcp/work-folder/katana_work_folder_mcp/search_hook.py` 存在
   - 两个 server 源码里能找到 `wiki_search_reindex` / `wf_search_reindex` 的 tool 定义
   - `wf_reindex` 仍然存在（未被顶掉）
   - `mcp/conftest.py` 的注入列表含 `"search"`；`mcp/run-tests.sh` 含 `search/tests`；
     `.github/workflows/tests.yml` 含 `-e mcp/search`
   - `mcp/kernel/`、`mcp/shared/`、`deploy/`、`docs/` 相对 base 零改动
   - 新增测试文件非空壳（无恒真断言）
