# E3 —— anchor-check 认 `web://`：按 bus 上的 transcript **离线**核验，并显式单列 `unsupported_scheme`

**目标仓**：`Dandi007/katana`
**目标文件**：`plugins/deep-research/skills/deep-research/loop-orchestration/tools/anchor-check.py`（240 行）
及其自检脚本 `anchor-check-selftest.sh`（同目录）。
**⛔ 改动面必须小：只扩核验器，不碰 plugin 仓、不碰 bus、不注册协议。**

---

## 0　⛔ 地面真相（派发方 2026-08-14 真机取证，照抄，不得推测、不得由 fixture 反推）

### GT-1　核验器现状：三类显式分类 + 四种退出码

`anchor-check.py` 逐字（节选）：

```python
CURRENT_URI_RE = re.compile(
    r'^code://([^@]+)@([0-9a-fA-F]{7,40})#L(\d+)(?:-L?(\d+))?$'
)
OLD_URI_RE = re.compile(r'^(?!code://)(.+):\d+(?:[-,]\d+)*$')

def classify(anchor):
    """返回 (类别, 解析结果)。类别 ∈ {current, old, unparseable}。"""
```

```python
    sums_ok = (cur_parsed + old_count + unparseable + discarded) == total and discarded == 0
```

```python
    # 退出码：
    #   0  → 无响亮失败 且 无现行格式未命中
    #   1  → 有现行格式引文未命中（校验失败）
    #   2  → 有响亮失败（缺 repo-root / fetcher 取不到 / 形态不合理）
    #   3  → 三类计数之和与输入条数不符（静默丢弃）
```

`--corpus` 支持 `bus:<channel>` 直连取全集；`code://` 的取材是
`git -C <repo_root> show <rev>:<relpath>`，且 fetcher 自检「非空且形态合理」，
失败即**响亮失败**（⛔ 不得当成「引文不匹配」）。

### GT-2　⭐⭐ `web://` 锚点的**真实**形态（今晚真机跑出来的，不是设计稿）

派发方 2026-08-14 06:17–06:22 跑通完整链路
（material → ingest → transcript → content-clue → triage → dispatch → `dr-worker-content` exit 0 → harvest），
证据 channel 上 16 条 anchor 逐字（此为 E1b 交付形态，scheme 由 **E1c** 修正为 `web://`）：

```
content://http://127.0.0.1:50287/e1-material2.png@9bee527fe5f6e5ddef93194f3ede333b964ff9b50c8db013aef1dc6659fe1675#L3:1-43
content://http://127.0.0.1:50287/e1-material2.png@9bee527f…#L7:12-308
content://http://127.0.0.1:50287/e1-material2.png@9bee527f…#L7:314-542
…（16 条）
```

**E1c 交付后的契约形态**（本包据此实现）：

```
web://<uri>@<digest>#<range>
```

三个部件的**实测事实**：

| 部件 | 事实 | ⛔ 陷阱 |
|---|---|---|
| `<uri>` | **未做 url-encode 的原始 URI**，本身含 `://`（如 `http://127.0.0.1:50287/e1-material2.png`） | ⛔ 不能用 `[^@]+` 或非贪婪匹配；spec 旧稿写的「url-encoded」**与实际产出不符**，以实际产出为准（派发方 2026-08-14 决定，理由：E1c 已交付未编码形态，且 persona 契约亦是未编码） |
| `<digest>` | **完整 64 位十六进制 sha256**（由 ingest 对取回字节权威计算，E1 交付） | ⛔ 不是 7–40 位的 git sha；⛔ 不得沿用 `code://` 的 `{7,40}` 宽度 |
| `<range>` | 实测 16/16 条**全部**是 `L<行>:<字符起>-<字符止>` 形态 | ⛔ 与 `code://` 的 `L<a>[-L<b>]` **不是同一套语法** |

`range` 语法实测分布逐字：

```
range 语法分布: {'L<a>:<c1>-<c2>': 16}
样本: ['L3:1-43', 'L7:12-308', 'L7:314-542', 'L11:28-84', 'L13:105-179', 'L13:189-269']
```

⚠️ E1c 只保证 `L` 前缀归一，**不归一内部语法**，所以 `L<a>` / `L<a>-L<b>` / `L<a>:<c1>-<c2>`
三种都可能出现，本包**都要认**。

⇒ 建议的解析锚点：**以结尾的定长 hex digest 为切分依据**、URI 段贪婪匹配
（形如 `^web://(.+)@([0-9a-fA-F]{64})#(.+)$`）。⛔ 但**必须自己取证确认**，不得照抄本行。

### GT-3　⭐ 核验数据源：**bus 上的 transcript，不重新联网抓取**

`web://` 的核验对象**不是线上网页**，而是 `research:content` 这条全局 channel 上那份
不可变的 `research.doc.v2`（`doc_kind="transcript"`）。按 `<digest>` 找到该 doc，
在其 `body` 里比对逐字引文 ⇒ **完全离线、确定性、可重放**。

真机上该 channel 的实况（测试总线 `http://127.0.0.1:7495`）逐字：

```
research:content  seq1  research.doc.v2
  doc_kind = transcript
  digest   = 63ac13abaabf5726e675d8fbb5ccda36a960767ba5b860448e701ada88f5e43b
  origin   = http://127.0.0.1:50287/e1-material.png
  body_len = 1008
```

⇒ 网页会改版，但我们核验的那份 transcript 不会。**`web://` 的核验硬度与 `code://` 同级。**

### GT-4　范围拍板：本期只核验 `code://` 与 `web://`

`wiki://` / `feishu://` 的 digest **不在** `research:content` 上（它们不走 ingest），
离线判据对其不成立。本期在核验器中**单列第四类 `unsupported_scheme`**：
透明计数、**不进 95% 分母**、条数写进报告头与运行记录。

⛔ 这不是分母作弊：防的是「取不到就悄悄不计」的暗箱，这里是**显式披露的类别**。

---

## 1　交付清单

| # | 必须交付 | 关键约束 |
|---|---|---|
| **D1** | `classify()` 认第四种 `web`：`web://<uri>@<64位hex>#<range>`，URI 段贪婪、digest 定长 64 hex | ⛔ 不得放宽 digest 宽度去兼容 `code://`；⛔ `code://` 与旧格式的分类结果**逐字不变** |
| **D2** | `range` 三种语法都要认：`L<a>`、`L<a>-L<b>`、`L<a>:<c1>-<c2>`（GT-2） | ⛔ 认不出的 range 语法 ⇒ **响亮失败**（exit 2 类），⛔ 不得静默当成「引文不匹配」，更不得当成命中 |
| **D3** | `web://` 的取材：从 `research:content` 按 `<digest>` 取 `doc_kind="transcript"` 的 doc，用其 `body` 做逐字比对（GT-3） | ⛔ **不得联网重新抓取 `<uri>`**（那是本包最重要的设计约束）。channel 与 bus 地址由**调用方显式提供**（与 `--repo-root` 同纪律：缺失即响亮失败，⛔ 绝不猜、绝不撞运气） |
| **D4** | `web://` 的 fetcher 自检与 `code://` 同构：取回的 transcript **非空且形态合理**（能定位到给定 range）；否则**响亮失败** | ⛔ 不得把「取不到 transcript」当成「引文不匹配」 |
| **D5** | 新增第五类计数 `unsupported_scheme`：非 `code://`/`web://` 的其它 scheme（`wiki://`、`feishu://` 等）**独立计数**，不进命中率分母（GT-4） | 与既有 `unparseable`（**根本解析不了**）**必须区分开**：`wiki://x@y#z` 是「格式合法但本期不支持」，不是「不可解析」 |
| **D6** | `sums_ok` 的守恒式扩到全部类别 | 逐字：`cur_parsed + web_parsed + old_count + unsupported_scheme + unparseable + discarded == total`；⛔ 任何一条都不得静默丢弃 |
| **D7** | JSON 输出与人读输出**都**新增：`web_parsed` / `web_verified_hit` / `web_failed` / `unsupported_scheme` 四个字段，并在报告头显式披露 `unsupported_scheme` 条数 | ⛔ 只加 JSON 不加人读输出不算交付 |
| **D8** | 退出码语义扩展但**不改既有含义**：`web://` 引文未命中 ⇒ 与 `code://` 同样归 exit 1；`web://` 的响亮失败 ⇒ exit 2；守恒不成立 ⇒ exit 3 | ⛔ `unsupported_scheme` **本身不得**导致非零退出（它是显式披露，不是失败） |
| **D9** | `anchor-check-selftest.sh` 同步覆盖新路径 | ⛔ 不得只加代码不加自检 |

## 2　验收判据

1. 仓内既有校验/测试命令**连跑两次全绿**（若无测试框架，则 `anchor-check-selftest.sh` 连跑两次全绿）。
2. **⭐⭐ D1/D2 判别性**：把 GT-2 那条**逐字的**真实 anchor
   `web://http://127.0.0.1:50287/e1-material2.png@9bee527fe5f6e5ddef93194f3ede333b964ff9b50c8db013aef1dc6659fe1675#L3:1-43`
   喂进 `classify()` ⇒ 判为 `web` 且解析出
   uri=`http://127.0.0.1:50287/e1-material2.png`、digest=`9bee527f…`（64 位）、range=`L3:1-43`；
   把 URI 段改成非贪婪或 `[^@]+` ⇒ **变红**（因为 URI 自身含 `://`）。
   另配两条：`#L9` 与 `#L7:12-308` 也都要解析成功。
3. **⭐⭐ D3 判别性（本包核心设计）**：预置一份 transcript（digest 与 anchor 一致、body 含该引文）⇒
   判为**命中**、计入 `web_verified_hit`；
   把同一条 anchor 的 digest 改成 `research:content` 上**不存在**的值 ⇒ **响亮失败（exit 2）**，
   ⛔ 不得记成「引文不匹配」。
   **⭐ 反向**：断言核验过程中**没有对 `<uri>` 发起任何网络请求**（GT-3）；
   改成联网抓取 ⇒ 该断言变红。
4. **⭐ D4 判别性**：transcript 取回为空 / range 定位不到 ⇒ **响亮失败**，⛔ 不得算未命中。
5. **⭐ D5 判别性**：喂 `wiki://foo@bar#L1` 与 `feishu://x@y#L2` ⇒ 计入 `unsupported_scheme`、
   **不**计入 `unparseable`、**不**导致非零退出；喂一条真正的乱码（如 `!!!not-an-anchor!!!`）⇒
   仍计入 `unparseable`。把两类合并 ⇒ 变红。
6. **⭐ D6 判别性**：构造含全部五类 + 一条缺 `anchor` 的记录 ⇒ `sums_ok=False` 且 **exit 3**；
   把守恒式漏掉任一类 ⇒ 变红。
7. **⭐ D2 反向**：喂一条 range 语法认不出的 `web://…#WHATEVER` ⇒ **响亮失败**，
   ⛔ 不得静默命中、⛔ 不得静默未命中。
8. **回归 ⛔**：`code://` 与旧格式 `path:line` 的分类、核验、计数、退出码**逐字不变**；
   `--repo-root` 缺失时对 `code://` 仍**响亮失败**。
9. **⭐ 判别性必须真正驱动被测对象**：⛔ 源码字符串匹配不构成证据；
   ⛔ 不得在测试里重实现一遍 `classify()`。
10. **Z1（真机，派发方执行）**：拿测试总线 `http://127.0.0.1:7495` 上真实的
    `research:content` 与本次跑出的证据 channel 作 corpus ⇒ 核验器给出
    `web_parsed`/`web_verified_hit` 非零、`sums_ok=true`，且**全程未联网抓取 `<uri>`**。

> 判据 10 由派发方在真机上验证。

## 3　⛔ 明确不做

| 不做 | 理由 |
|---|---|
| 联网重新抓取 `<uri>` 做比对 | GT-3：核验对象是 bus 上不可变的 transcript，不是线上网页 |
| 核验 `wiki://` / `feishu://` | GT-4 已拍板：本期单列 `unsupported_scheme`；硬化等那两条信源真走通再定 |
| 改 plugin 仓（anchor 拼装、harvest、ingest） | **E1c** 的范围 |
| 注册 protocol / 建 channel / 往任何 channel 写入 | 核验器是**只读**工具（文件头逐字：「⛔ 只读：本工具不向任何 channel 写入」） |
| 改 `code://` 的正则宽度、旧格式判定、退出码既有含义 | 判据 8 |
| 收工仲裁者 / 原子产物 / 驱动入口重写 | E5 / E4 / E7 |

## 4　运行环境前提（派发方已就位）

测试总线 `http://127.0.0.1:7495`（独立 SQLite，与生产 7490 零共享）：
`research:content` 已建且已有真 transcript；证据 channel 上已有 16 条真 `web://`（E1c 后）锚点。
⚠️ 生产总线 `127.0.0.1:7490` 一个字节不许写。

## 5　评审口径

- **REJECT 只用于 blocker 级**：交付清单缺项、判据不成立、判别性缺失或方向钉反
  （尤其判据 3 的「不得联网」与判据 5 的两类不得合并）、越出 §1 范围、改坏判据 8 的既有行为。
  文风与偏好写成 non-blocking 建议。
- ⚠️ 本线累计因「测试绕开被测对象」被驳回 10 次以上。**判据 2–7 的测试必须真正驱动被测对象。**
- ⚠️ 另注意：本线曾多次出现「为观察不到的产物发明契约、再写 fixture 迎合它」。
  §0 的 anchor 与 range 样本**都是真机跑出来的**，⛔ 不得改造它们去迁就实现。
- reviewer 只读，判据 1–9 由 acceptance 命令的执行结果作证。
- ⛔ 实现者不得写 `.dd-evidence/**` 与 `.dev-dispatch/**`。
