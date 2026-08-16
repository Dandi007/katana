# H7 —— goal worker 座位对 work-folder MCP 的**写面收窄**（路线 (b)：server 内加等价闸）

- 单：`dev_katana_h7_wf_scope_narrowing_01`
- 仓：`Dandi007/katana`
- base：`refs/heads/release/katana-h7-wf-scope-narrowing`（切点 `dedbb049d2be75a4f228b20ddb566e629fb5a2f3` = `release/katana-infra` tip）
- spec 成文：2026-08-16T03:2xZ
- 判据口径：**相对当刻基线无新增失败**；先红后绿，红判据必须先跑出红。

---

## §0 路线已裁定：**只做 (b)，不做 (a)**

**监督者通报 14 · 2-2 逐字**：

> **2-2 H7 路线：先 (b) server 内加等价闸，(a) 前置 katana_remote 转监督面择期统一 backlog。**
> 理由照准 worker 分析：(a) 部署面牵动全部消费方（≥3 在跑泵+dd 座位）且上线瞬间未配 token 座位当场失能——不适合边跑边改窗口；(b) 灰度 off 时行为不变、回滚一个开关。长期统一到 (a) 由监督面在考卷全收官后另起。

⇒ 本单**只交付 (b)**：在 `mcp/work-folder` 的 server 内加一层等价闸。
**(a)「前置 `katana_remote`」不在本单范围内**，越界即 REJECT（见 §5）。
具体地：**不得**改 `katana-work-folder-mcp.service` 的启动方式，**不得**要求任何消费方改 URL 或加 token。

---

## §1 靶面

goal worker 座位对 work-folder MCP 拥有**全量写面**，包括建卷、全局索引重建、删除/搬运等
远超「记账」所需的能力。本单按座位收窄写面，**读面一律不动**。

---

## §2 要求

### R1 —— 座位只保留「记账所需」的最小写面（**deny-by-default**）

| 工具 | 判定 | 依据 |
|---|---|---|
| `wf_append_progress` | ✅ 允许 | 每轮记账 |
| `wf_resume` | ✅ 允许 | 每轮开工读状态（虽在 `_MUTATE_OPS` 内，语义是读+校验） |
| `fs_create` / `fs_write` / `fs_edit` | ✅ 允许 | 落 spec 与固化证物 |
| **`wf_save`** | ## ✅ **允许（已裁定）** | **通报 14 · 2-3 逐字：「wf_save 待定项裁定：留在允许集合内（它是 checkpoint 主工具，其他消费方依赖；审计数据出来后若确认本卷不用再收窄）」**。⚠️ v2 spec 原稿标「⚠️ 待定 / 建议先禁」——**该建议已被裁定推翻，实现须按「允许」落地** |
| `wf_create` | ❌ 禁 | 建新考卷是监督面动作 |
| `wf_reindex` | ❌ 禁 | 全局索引重建，影响面超出单卷 |
| `fs_delete` / `fs_rename` / `fs_copy` / `fs_batch` | ❌ 禁 | 删除/搬运不可逆 |

- 判定必须 **deny-by-default**（沿用 `mcp/remote/scopes.py` 既有原则）：
  **未列入任何集合的工具名 → 拒绝**。
- 拒绝必须返回**稳定错误码 + 被拒工具名 + 该座位允许的集合**，
  不得只回 generic error（否则 worker 无法自我纠正）。

### R2 —— 灰度开关，**默认 `off`（只写审计不拦）**

**监督者通报 14 · 2-3 逐字**：

> **2-3 灰度默认：off（只审计不拦）。** 允许集合未经真实调用分布验证前默认 on=拿生产当试验场，且「worker 记不上账」是最难归因的失败形态。……真拦截的开启另行拍板，以审计数据为据。

- 开关默认值**必须**是 `off`。
- `off` 时行为与当刻**逐字一致**（全放行），只额外写审计。
- ## **本单不得把默认值设为 `on`，也不得提供「自动升级为 on」的逻辑。**
  真拦截的开启是**另行拍板事项**，交付物只提供开关本身。
- 回滚形态：改一个开关回 `off` 即恢复，**不需重启任何消费方**。

### R3 —— 每次拒绝（及 `off` 时的每次「本应拒绝」）必须可观测

审计事件必须含：`principal`/座位、`tool`、`folder_id`、`decision`、`allowed_set`。
`off` 时也要写，否则拿不到「真开起来会拦掉什么」的数据——而通报 14 明写
「真拦截的开启……**以审计数据为据**」，这份数据就是本单的核心交付。

---

## §3 先红后绿判据

**工装文件**：`mcp/remote/tests/test_wf_scope_narrowing.py`
**运行命令**：§4 `acceptance_commands` 第三条（先红后绿两侧同一条）

| # | 断言 | 在 `target_base_commit` 上 | 类 |
|:--:|---|---|---|
| 1 | 允许集合内（`wf_append_progress` / `fs_edit`）→ 放行 | 绿（当刻无闸，什么都放行） | ②，须配变异点 |
| 2 | 禁止集合内（`wf_reindex` / `fs_delete`）→ 拒绝，错误含工具名与允许集合 | **必红** | ① |
| 3 | **deny-by-default**：未列入任何集合的新工具名 → 拒绝 | **必红** | ① |
| 4 | 灰度开关 `off` → 全放行但写审计 | **必红**（当刻无该开关） | ① |
| 5 | 灰度开关 `on` → 按 R1 表拦截 | **必红** | ① |
| 6 | 三个必需工具在 `on` 时仍放行 | **必红**（依赖 4/5） | ① |
| **7** | **`wf_save` 在 `on` 时仍放行** | **必红** | ① · **本条为通报 14 · 2-3 裁定新增，原 v2 spec 无** |
| 8 | **变异**：deny-by-default 改成 allow-by-default → 用例 3 必须转红 | 变异证据 | — |
| 9 | **变异**：`on` 前提下把 R1 允许集合改空 → 用例 1、6、7 必须转红 | 变异证据 | — |

**②类收口**：用例 1 归②类不归③空绿 —— 它绿的原因是「当刻根本没有闸」，
而非「闸判定后放行了」。R1/R2 落地后它就是一条实打实的守卫断言，
且**可以**被变异打红（用例 9），按 F-5 就**必须**配。
> 若归③空绿，等于承认「允许集合被改坏也没有任何用例会红」——
> 而这条恰恰是 deny-by-default 语义里**唯一**保护「必需工具不被误伤」的断言。

**用例分类清点**：①类 6 条（2/3/4/5/6/7）、②类 1 条（1，已配变异点 9）、
③类 0 条、变异专供 2 条（8/9）。**②类缺口计数 = 0。**

**真机先红的额外要求**：生产 5602 实测**绕过** `katana_remote`
（直起 `python -m katana_work_folder_mcp.server`），故先红必须**另附一条
「生产形态下当刻无任何 scope 判定」的回显**，否则无法证明这条闸真的不存在。
**该回显由 implementer 在其工装内构造。**

**证据载体**：三类回显落 `.dd-evidence/<attempt_id>/h7-scope.md`，随交付提交进被审树。

---

## §4 验收栈与基线

**验收栈**取自 `.github/workflows/tests.yml` 的 `mcp-unit` job（该仓 merge gate）。

⚠️ **注意**：`mcp/run-tests.sh` 用 `PY="${PYTHON:-python3}"`，依赖 `PYTHON` 环境变量；
而 dd acceptance 是 `env_allowlist=["PATH","HOME"]` 的极小环境、argv-only 无 shell，
**无法传该变量**。故按 v2 spec §3.1 的指示采用**展开形态**，逐字展开 `run-tests.sh` 的七个路径。

`acceptance_commands`（本单实际使用）：
```json
[{"argv":["uv","pip","install","--python",".venv/bin/python","-e","mcp/shared","-e","mcp/kernel","-e","mcp/memory","-e","mcp/wiki","-e","mcp/work-folder","pytest"]},
 {"argv":["./.venv/bin/python","-m","pytest",
          "mcp/shared/tests","mcp/wiki/tests","mcp/work-folder/tests","mcp/memory/tests",
          "mcp/migration/tests","mcp/kernel/tests","mcp/remote/tests",
          "--import-mode=importlib","-p","no:cacheprovider"]}]
```
`setup_commands`：`[{"argv":["uv","venv"]}]`

**为什么 `mcp/remote` 与 `mcp/migration` 不在 install 列表里仍能跑**：
`mcp/conftest.py` 显式把 `remote` 等 8 个子包插进 `sys.path`
（注释原文：「ensure mcp/ packages are importable without pip install」）。
且 `grep -rn 'skip\|importorskip' mcp/remote/tests/*.py` **零命中** ⇒ 缺依赖会直接 error，不会静默 skip 造假绿。

**基线**（在本单 `target_base_commit = dedbb049` 上同环境实取）：
**`1403 passed` / 具名失败集合 = ∅（空集）**。
⇒ **差集 = 候选具名失败集 − ∅ = 候选具名失败集**，
即 **accept-green ⟺ 候选具名失败集为空**，无可继承的红。

**差集口径**：具名失败集逐条比对，`grep -E '^(FAILED|ERROR) ' | sed -E 's/^(FAILED|ERROR) //; s/ .*//' | sort -u`。
`sort -u` 是硬要求。**禁一切数量口径**（「passed 数变多/变少」不构成结论）。
**同环境是硬要求**：基线与候选必须在同一环境取。

---

## §5 非目标（越界即 REJECT）

1. ## **不做路线 (a)** —— 不前置 `katana_remote`、不改 `katana-work-folder-mcp.service`
   启动方式、不要求任何消费方改 URL 或加 token。（通报 14 已把 (a) 转监督面 backlog）
2. ## **不得把灰度默认设为 `on`**，不得提供自动升级为 `on` 的逻辑。
   真拦截的开启属**另行拍板**事项。
3. ## **不得把 `wf_save` 移出允许集合**（通报 14 · 2-3 已裁定其留在允许集合内）。
4. **不动读面**：`fs_read` / `fs_list` / `fs_stat` / `wf_list` / `wf_search` 一律不改。
5. 不改 `mcp/remote/scopes.py` 已有六个 scope 定义与 `_MUTATE_OPS` 枚举。
6. 不做跨仓 token 分发流程；不改 wiki 域 `DomainPolicy`；不碰 `.dev-dispatch/**`。

---

## §6 交付前自检

- [ ] 用例 2/3/4/5/6/7 在 base 上**先跑出红**，红在哪个断言逐条点名
- [ ] 两个变异点（8/9）各出一份变异回显
- [ ] 「生产形态下当刻无任何 scope 判定」的回显已附
- [ ] 灰度开关默认值经代码与测试双向确认为 `off`
- [ ] `wf_save` 在 `on` 时放行（用例 7）
- [ ] 具名失败集差集为空（基线 ∅，故候选须为 ∅）
- [ ] 三类回显落 `.dd-evidence/<attempt_id>/h7-scope.md`
