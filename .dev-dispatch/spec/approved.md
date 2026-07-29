# Dev Spec — katana wiki v2 MCP server (`dev_wiki_v2_01`)

status: approved
date: 2026-07-30
repo: Dandi007/katana
spec_revision_id: specrev_wiki_v2_001

## 0. 一句话目标

在 katana repo 新增 `mcp/wiki-v2/` 包：一个平铺页面、稳定 ID、单门写入、内嵌 hybrid 检索的 wiki MCP server，外加把 v1 数据（Zettelkasten zone）迁移到 v2 数据 repo 的迁移 CLI。**不改动现有 `mcp/wiki/`（v1）任何行为**，v1 在迁移完成前继续作为生产保底。

设计背景（浓缩）：2026-07-29 全链路调研（work folder `wf-f94c09`）证明 v1 结构性失效——治理写入口 `wiki_ingest_plan/apply` 在可观测窗口真实调用 0 次，唯一批量建库走了同 server 的 `fs_write` 旁路；检索外包给 multi-root 共享索引器（vault-search/vault-indexer）导致 82% 跨域污染与向量索引静默死亡 15 天。v2 的三条根治：**旁路在工具面上不存在**、**identity 与路径解耦**、**检索收回进程内且降级显式**。

## 1. 交付物（全部在本 repo 内）

1. `mcp/wiki-v2/` 新 Python 包：`katana_wiki_v2_mcp`（pyproject 名 `katana-wiki-v2-mcp`，console script `katana-wiki-v2-mcp`），布局对齐 `mcp/wiki/` 的既有惯例（fastmcp server + tests/）。
2. 迁移 CLI：`katana-wiki-v2-migrate`（同包内 `migrate.py`），从 v1 wiki 数据 repo 生成 v2 数据 repo。
3. `mcp/wiki-v2/tests/` 完整 pytest 覆盖（见 §8 验收）。
4. 按仓库惯例在 `tests/reports/` 落一份本次测试报告 md。

### 非目标（硬边界）

- 不修改 `mcp/wiki/`（v1 server）、`mcp/kernel/`、`mcp/shared/` 的任何现有行为；如需 shared 能力优先 import 复用，不适配时在 `mcp/wiki-v2/` 内自持实现
- 不修改 `plugins/`（skill 一代退役是后续独立工作）
- 不修改任何 systemd unit / 部署配置；不触碰 `/data/wiki`、`/data/vault` 等生产数据（迁移 CLI 只在测试 fixture 上验证；真实迁移由 operator 事后执行）
- 不实现 web UI；不实现 vault-search/vault-indexer 的任何对接（v2 与它们零依赖）

## 2. v2 数据 repo 模型

迁移 CLI 的输出、server 的 data_root，都遵循：

```
<data_root>/                # 独立 git repo（server 初始化时若非 git repo 则报错拒启）
├── WIKI.md                 # schema 说明（meta 文件，豁免页面校验）
├── log.md                  # gap/ingest 日志（meta 文件，豁免）
├── pages/                  # 唯一内容目录，平铺，禁止子目录
│   └── <标题>.md
└── .katana/
    ├── manifests/          # 每次写入的服务端账本（JSON，一写一文件）
    └── index/              # lancedb + keyword 索引（在 .gitignore 中，可全量重建）
```

页面 frontmatter（YAML）：

| 字段 | 必填 | 约束 |
|---|---|---|
| `id` | 是 | `w-` + 6 hex，server 签发，创建后不可变，全库唯一 |
| `摘要` | create 时必填 | ≤100 字 |
| `类型` | create 时必填 | 枚举：卡片 / 索引 / 源码分析 / 架构 |
| `source_type` | create 时必填 | 枚举：human / mixed / llm |
| `credibility` | create 时必填 | 枚举：high / medium / low |
| `tags` | create 时必填 | 非空 list |
| `创建日期` | create 时必填 | YYYY-MM-DD |

- 文件名（不含 .md）= 标题 = wikilink target，全库唯一（server 强制，含大小写敏感精确匹配）
- 标题禁止字符：`/`、换行、前后空白；允许中文、空格、常见标点
- provenance：create 时正文须含 `# References` 节或 frontmatter `sources` 非空（二者其一）

## 3. MCP 工具面（唯一工具面，无其它写路径）

### 读

- `wiki_get(ref)`：`ref` 接受 `w-xxxxxx` 或标题；返回 `{id, title, frontmatter, body, inlinks: [标题], outlinks: [标题]}`；未命中返回 `NOT_FOUND`
- `wiki_read(ref, offset?, limit?)`：cat -n 语义的原始文本（构造 `wiki_edit` 的 old_string 前用它取精确文本）
- `wiki_list(prefix?, limit?, cursor?)`：分页列出 `{id, title, 摘要}`

### 检索

- `wiki_search(query, top_k=10)`：进程内 hybrid（RRF 融合 keyword + vector）；返回 `{results: [{id, title, score, snippet}], index_health}`；结果只可能来自 pages/
- `wiki_query(query)`：fat 检索——search 之后附带候选逐条内容与自评指引（沿用 v1 于 2026-07-29 PR #98 落地的「模型逐条自评 + report_gap」协议文本，从 `mcp/wiki/katana_wiki_mcp/query.py` 迁移语义，不发明新协议）
- `wiki_report_gap(query, note?)`：追加 gap 记录到 `log.md`（meta 写路径，自动 commit）

### 写（每次调用 = 校验 → 写文件 → 更新索引 → git commit + manifest，全同步）

- `wiki_create(title, body, frontmatter)`：ingest-grade 全量校验（§2 表 + provenance + 至少 1 条 outlink 或显式 `allow_no_outlink=true`）；标题已存在 → `TITLE_EXISTS`（返回既有页 id + 摘要）；成功返回 `{id, path}`
- `wiki_update(ref, body, frontmatter?)`：整页替换，edit-grade（见 §4）
- `wiki_edit(ref, old_string, new_string)`：精确文本替换，edit-grade；old_string 不唯一/未命中 → 结构化拒绝
- `wiki_rename(id, new_title)`：改标题 + 文件名，**服务端重写全库入链**（`[[旧标题]]` 与 `[[旧标题|别名]]` 两种形态），单 commit 原子完成；目标标题已存在 → 拒绝
- `wiki_delete(id, force=false)`：有入链且非 force → 拒绝并列出全部引用页标题；force 时必须同时传 `inlink_action`（`remove_links`＝把引用页中的 `[[标题]]` 降为纯文本）并在同一 commit 内完成
- `wiki_ingest_plan(sources)` / `wiki_ingest_apply(plan)`：**仅批量导入**场景；plan 做判重（对每个候选给出 create-vs-已存在近似页），apply 逐页走与 `wiki_create` 同一套校验代码路径（不得复制第二份校验实现）

### VFS（只读四件套）

- `fs_read` / `fs_list` / `fs_glob` / `fs_stat`，语义对齐 `mcp/wiki/katana_wiki_mcp/fs_tools.py` 的只读子集
- **不存在** `fs_write/fs_create/fs_edit/fs_copy/fs_rename/fs_delete/fs_batch`——这是 INV-1 的工具面体现

### meta 文件（WIKI.md / log.md）

- 经 `wiki_meta_write(name, content)` 单工具覆盖写（name 枚举仅这两个），宽松校验（非空 UTF-8），同样 commit + manifest

## 4. 校验分级

- **ingest-grade**（wiki_create / ingest_apply）：§2 表全部必填 + provenance + outlink 要求
- **edit-grade**（wiki_update / wiki_edit）：结果页不得比操作前**新增**违规项（缺失字段可保持缺失，但不得新增缺失；`id` 不得变；标题经此路径不得变——改名只走 rename）；摘要如被修改则须 ≤100 字
- client 在任何写工具中提交与存量不符的 `id` → `REF_MISMATCH` 拒绝；create 提交 `id` 字段 → 拒绝（id 只能 server 签发）

## 5. 不变量（测试必须逐条固化）

- **INV-1 单门写入**：list_tools 输出中不存在任何能绕过校验写 pages/ 的工具
- **INV-2 identity**：id server 签发、唯一、不可变、不可伪造；标题任意时刻全库唯一
- **INV-3 链接不因操作而断**：rename/delete 完成后，库内不存在由该操作新增的 broken wikilink；create/update 引用不存在页允许（「待写概念」语义），不算违规
- **INV-4 索引同步且降级显式**：写调用返回前索引已更新；embedding 失败不阻塞写（该页标 degraded，仅入 keyword 索引）；一切检索响应含 `index_health: {mode: hybrid|keyword_only, degraded_pages, last_error}`，`last_error` 保留原始异常文本而非类名
- **INV-5 每写一 commit + manifest**：任何 mutation 恰好产生一个 git commit 与一个 `.katana/manifests/` 账本文件（记录 tool、changed_paths、时间戳）；工作区在任意两次调用之间 `git status --porcelain` 为空
- **INV-6 坏页隔离**：pages/ 中存在无法解析 frontmatter 的文件时，只有针对该页的操作报错，其它页读写与全局检索不受影响（v1 曾因 `_quarantine` 未排除导致全局写锁，此为反向约束）

## 6. 检索实现

- lancedb 以 Python 库形态内嵌（`lancedb` 依赖），表存 `<data_root>/.katana/index/`；keyword 侧自持实现（分词可用简单 n-gram/jieba 任选，测试只断言召回行为不断言算法）
- embedding client：OpenAI-compatible `POST {base_url}/v1/embeddings`；配置项 `base_url` / `api_key_path` / `model` / `dim` 经 server 配置注入（生产值：`http://172.22.62.133:18081`、`BAAI/bge-small-zh-v1.5`、dim 512——写入默认配置但**测试不得依赖网络**）
- embedding client 必须可注入替身：单测用确定性 fake embedder 跑通 vector 路径；网络失败路径用注入的抛错 embedder 覆盖
- 全量重建命令：`katana-wiki-v2-mcp --rebuild-index`（或等价子命令），遍历 pages/ 重算两路索引

## 7. 迁移 CLI contract（`katana-wiki-v2-migrate`）

```
katana-wiki-v2-migrate --source <v1-wiki-repo> --dest <v2-repo-dir> [--dry-run]
```

- 收录：`<source>/Zettelkasten/**/*.md`，**排除**目录名以 `.` 开头的子树（如 `.audit/`）与 `_quarantine/`
- 平铺：取 basename 为标题（`Zettelkasten/Index/机器学习索引.md` → `pages/机器学习索引.md`）
- 标题冲突：**不猜测**，全部收进 `<dest>/migration-conflicts.json` 报告并以非零码退出（--dry-run 同样输出报告）；零冲突才产出完整 repo
- 每页签发 `id` 写回 frontmatter（已有合法 `id` 则保留）；缺失的其它 frontmatter 字段**保持缺失**（存量债不在迁移中修，v2 的 edit-grade 允许其存在）
- wikilink 规整：`[[Index/xxx]]`、`[[Zettelkasten/xxx]]` 等带路径形态 → `[[xxx]]`（含 `|别名` 形态）；纯标题链接不动
- 产出物：git init + 全量 initial commit、`WIKI.md`（从 source 复制）、空 `log.md`、`.gitignore`（含 `.katana/index/`）、迁移摘要 `migration-report.json`（页数、链接改写数、id 签发数、跳过清单）
- 幂等：对同一 source 重跑产出逐字节相同的 pages/ 内容（id 签发用内容哈希派生或显式 seed，禁止随机）

## 8. 验收（reviewer 逐条核，实现者先自证）

测试运行方式：`cd mcp/wiki-v2 && uv run --extra dev pytest -q`（或仓库 `mcp/run-tests.sh` 若其机制可直接纳入新包）。

- [ ] A1 每个写工具三态测试：校验拒绝 / 成功 / manifest+commit 落账（INV-5 断言 commit 数与 manifest 数精确 +1）
- [ ] A2 rename 回归：A←B/C 两页引用（含别名形态），rename 后 B、C 内链接已重写、旧标题 `wiki_get` 返回 NOT_FOUND、新标题与 id 均可取、broken link 增量 = 0
- [ ] A3 delete 回归：有入链拒绝并列出引用页；force + remove_links 后引用页内链接降为纯文本，同一 commit
- [ ] A4 INV-1：list_tools 断言 mutating fs_* 不存在；INV-2：伪造/篡改 id 的写调用全部 REF_MISMATCH
- [ ] A5 检索：fake embedder 下 hybrid 返回且 `index_health.mode == "hybrid"`；抛错 embedder 下写入成功、该页 degraded、`mode == "keyword_only"` 且 `last_error` 含原始错误文本
- [ ] A6 INV-6：注入坏 frontmatter 页后，其它页 create/update/search 正常，全局无写锁
- [ ] A7 迁移：fixture（含 Index/ 子目录、带路径 wikilink、标题冲突用例、已有 id 用例、`.audit/` 干扰文件）上跑 migrate——冲突报告、平铺、链接规整、id 幂等逐项断言；migrate 产物直接被 v2 server 打开并通过 `--rebuild-index` + `wiki_search` 冒烟
- [ ] A8 并发写串行化：两个并发 mutation 不产生交叉污染 commit（server 内单写锁）

## 9. 实现约束

- Python ≥3.11，依赖对齐 mcp/ 既有生态（fastmcp、httpx、pyyaml）+ `lancedb`；复用 `katana-kb-mcp-shared` / `katana-kernel` 中适配的机制（VFS 语义、manifest/commit 事务）优先于重写，但不得为复用而修改它们
- 错误返回结构化：`{code, message, detail?}`，code 用本 spec 出现的枚举（NOT_FOUND / TITLE_EXISTS / REF_MISMATCH / VALIDATION_FAILED / DELETE_BLOCKED …）
- 禁止在 server 内静默吞异常：所有 except 必须保留原始异常文本进日志或返回体（v1 向量索引静默死 15 天的直接根因）
- 代码风格对齐 `mcp/wiki/` 现状；新增文件不引入与本 spec 无关的重构

## References

- 设计 spec 源头：work folder `wf-f94c09/spec.md`（2026-07-29，含 8 条决策与排除理由）
- v1 现状调研：`wf-f94c09/findings.md` 18 节
- v1 代码基线：本 repo `mcp/wiki/katana_wiki_mcp/`（含 PR #98 已合入的 edit-grade/ingest-grade 分级与 query 自评协议）
