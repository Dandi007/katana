# dev_katana_wf_verify_typing_01 —— work-folder 环境验证按资源类型判定，别把端点/记法当本地路径

```
development_id: dev_katana_wf_verify_typing_01
status: ready-to-dispatch（先红证据已备）
date: 2026-08-14
repo: katana（单仓；本单不碰 goal-agent / agent-runtime）
target_base: 由派发方按 katana 线分支实况定（本卷不代定）
upstream: 考卷 C wf-9f3cfe 台账 G3
先红证据: evidence/g3-red-20260814/（真机 echo，工装 ops/g3-red/probe.py）
换号核对: dd attempts 无同名目录、三仓无同名 loopdev 分支（2026-08-13T18:0xZ 实核）
```

## 1. 先红（真机实测，非推理）

`ops/g3-red/probe.py` 直接 import 生产同一份
`katana/mcp/work-folder/katana_work_folder_mcp/verify.py`，在内存里喂四行样例表：

```
parse_context_paths 解析出 4 条资源：
  name=反引号包裹的真实目录     path='`/data/work-records`'
  name=dd Development MCP 端点  path='http://127.0.0.1:5606/mcp'
  name=家目录记法               path='~/.claude'
  name=裸真实目录（对照）        path='/data/work-records'

  反引号包裹的真实目录  -> BROKEN   路径不存在: `/data/work-records`
  dd Development MCP 端点 -> BROKEN 路径不存在: http://127.0.0.1:5606/mcp
  家目录记法            -> BROKEN   路径不存在: ~/.claude
  裸真实目录（对照）     -> DRIFT    有未提交变更

overall_level = BROKEN
G3-RED: CONFIRMED
```

对照项判 DRIFT（而非 BROKEN）⇒ 探针不是恒红，红的确实是那三类。

**断链定位（读码实核）**：`verify.py:79-101` 的 `parse_context_paths` 把表格第 2 列
**原样**取出（只跳过空/`<` 开头/含"路径|地址"字样三种），`fs_git_probe`（:199）随即
`os.path.exists(path)`。⇒ 反引号、URL、`~`、`repo:path` 一律被当成本地路径 stat。

**后果（上游真机实证，本卷 backlog G3 已载）**：B 线泵 run `gdpump-20260813-211358-1a0446`
第 7 轮 coordinator 调 `wf_resume` 得 `BROKEN` → 按「BROKEN 必停」契约裁 blocked → **整泵终局**。
即：一张写得规范（带反引号）的 context 表，能把一条在跑的泵杀死。

## 2. 变更契约（只动 katana work-folder MCP）

**2.1 资源分型.** `parse_context_paths` 产出的 `Resource` 增加类型判定（实现形态由实施方裁定）：
- `local-path`：以 `/` 或 `./` 开头，或 `~` 开头（**须先 `expanduser`**）→ 走今天的 fs/git 探测；
- `endpoint`：`http://`、`https://`、`ws://` 等 scheme → **不 stat**；探活与否见 2.3；
- `repo-ref`：`repo:path` 一类记法 → 不 stat，标记为 client-verified；
- `unknown`：其余 → 不 stat，判 `INFO`，**不得**因它把 overall 拉成 BROKEN。

**2.2 装饰字符归一.** 第 2 列先剥 markdown 装饰再判类型：成对反引号、加粗 `**`、行内链接
`[text](path)` 取 path。**这条是先红里最致命的一条**——写法规范反而被判死。

**2.3 端点探活是可选项，不是默认.** 默认只标 `endpoint` 不探活；若实现探活，必须
超时 ≤2s、失败只降级为 `DRIFT`，**不得**产生 `BROKEN`（网络抖动不该杀泵）。

**2.4 overall 语义收窄.** `overall_level` 只由 `local-path` 类资源的 verdict 决定；
其余类型最多贡献 `DRIFT`。**`BROKEN` 必须意味着「卷内声明的本地路径真的不在」**。

**2.5 非目标.** 不动 `wf_resume` 的对外契约与返回结构；不动 guard-scope（G1/katana#116）；
不改 context.md 的书写规范去迁就实现（**修实现，不是修所有卷的文档**）。

## 3. 验收标准（可机检）

### 3.1 单测
1. 反引号包裹的存在路径 → `MATCH`（先红为 BROKEN）。
2. `http(s)://` → 不调用 fs 探测（用 fake probe_fn 断言**未被调用**），且不产生 BROKEN。
3. `~/<存在目录>` → expanduser 后 `MATCH`。
4. 不存在的裸路径 → 仍 `BROKEN`（**回归护栏**：不能为了不误杀就永不判死）。
5. 混合表 → `overall_level` 只受 local-path 影响。

### 3.2 真机后绿（同一工装）
`python3 ops/g3-red/probe.py` 重跑，末行须变成 `G3-RED: NOT_CONFIRMED`（先红三条转 MATCH/INFO）。
> 该工装是**先红/后绿同一份**，判据方向相反即可，勿另写一份。

### 3.3 回归
B 卷 `wf-3c3dba` 迁去 `env-host.md` 的宿主资源表可迁回 context.md 而不再触发 BROKEN
（属观察项，不作通过条件——迁回与否是那一卷的事）。

## 4. 交付边界
代码与 code review **全部走 dev-dispatch**。本 worker 只产出本 spec、先红证据与 `ops/g3-red/` 工装。
**入队次序与授权不在本卷自决**：katana 仓不在授权硬线 1 的两仓之内，派发前需上游拍板（见 questions Q6-1）。
