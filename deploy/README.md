# katana MCP 容器化部署（零 bind mount）

把 `memory` / `wiki` / `work-folder` 三域的 MCP 服务跑在容器里，**数据在 named volume，代码烘进镜像**——整套部署里没有任何一个 bind mount。

条款：[`docs/constitution/002-data-plane-privacy.md`](../docs/constitution/002-data-plane-privacy.md)。设计与全局进度：work folder `wf-77510c`。

## 为什么零 bind

要防的不是「有权限的人故意越狱」——本机 uther 有免密 sudo，任何方案他都能绕过，那个维度上比较没意义。

要防的是**事故**：某条工作线的观测进程把 `--outdir` 指进了受治理的仓（20 秒一张 snapshot、计划跑 24 小时），agent 顺手 `git commit`。实测代价：work-folder MCP 因 clean-repo 前置条件 12 小时内拒绝 150 次，还得靠人工「代提交脏文件」扫地。

named volume 把这两类事故**结构性消除**：宿主命名空间里根本不存在那三个路径，写无可写——不是「写的时候报错」，是**没有靶子可指**。这比「路径还在、只是没权限」少一个能被误指的目标。

代码同样不 bind：全部烘进镜像。于是「data root 是 MCP 进程的私有成员」这句话在部署层面是字面成立的。

## 组成

| 文件 | 作用 |
|---|---|
| `../mcp/Dockerfile` | 多阶段构建，一个镜像三个服务 |
| `../mcp/requirements.lock.txt` | 68 个包全 hash 钉死，`--require-hashes` 安装 |
| `../.dockerignore` | context 只放 `mcp/` 下五个包 |
| `docker-compose.yml` | 三服务 + 三 named volume，零 bind |
| `migrate-data-to-volumes.sh` | 宿主目录 → 卷，带校验与安全网 |
| `backup-volumes.sh` | 卷 → 宿主 bare mirror |
| `dd-stall-probe.sh` | **非部署件**：判定一条 dev-dispatch implement attempt 是否卡死（只读、无副作用、带正反自证） |
| `dd-dispatch-preflight.sh` | **非部署件**：派 dd 单前的前置校验，选择器双层验证 + H0 不变量；不通过就不输出 initial_handoff |

`dd-stall-probe.sh` 与容器化无关，放这里是因为它和 `rehearse.sh` 同属本卷的确定性
ops 工具面。它存在的原因是：本目标的接线单先后两次卡死，而两次都是靠人盯表判断
「还在不在跑」，中间用错过两个信号——「进程还在」（卡死 72 分钟里进程一直在）和
「socket 句柄数为 0」（实测三个**健康**作业同样是 0，这个信号根本不成立）。
真正能区分的只有产出，故判据是「run target 零写入时长 + nodes/ 是否为空」，
阈值 30 分钟按实测标定（健康 implementer 节点完成耗时 988s ≈ 16.5 分钟）。
`--self-test` 用合成的卡死夹具与真实已收束的 run 做正反两例自证。

`dd-dispatch-preflight.sh` 解决的是另一条、已经吃掉**两个 development id** 的坑：
选择器有两层互不一致的注册表（engine-frozen 解析器 / agent-run 的 model-registry），
`dsv4pro/lingzhi` 只过前者、`ds/lingzhi` 只过后者，两次都要到派发时才炸，而
attempt-context/v1 **不支持 reconfigure**，只能换号重派。

把教训写进 PR 说明是挡不住的——下次派单没人会去读上一张单的 PR。所以做成流程里绕不过去
的一步：**`initial_handoff` 的 JSON 只从这个脚本出**。任一层不认，脚本非零退出且
**不打印 handoff**，那份 JSON 就不存在，也就无从粘进 `development_create`。
它同时校验 H0 的三条不变量（worktree 干净、symbolic-ref 匹配、唯一父 = target base）。
`--self-test` 直接拿两个真实烧过号的选择器做反例。

## 构建

**两个镜像都要建，且必须同 tag。** compose 里 `katana-mcp` 与 `katana-embedding` 共用同一个
`KATANA_MCP_TAG`，只建一个的话另一个在步骤 0 就把演练挡下来。

```bash
cd <repo>
REV=$(git rev-parse --short HEAD)

# 1) 三域 MCP（context 由 .dockerignore 收到 mcp/ 下六个包）
docker build -f mcp/Dockerfile --build-arg GIT_REVISION=$REV -t katana-mcp:$REV .

# 2) 共享 embedding（context 是 services/embedding，不是仓根——模型在构建期烘进去）
docker build -f services/embedding/Dockerfile --build-arg GIT_REVISION=$REV \
             -t katana-embedding:$REV services/embedding
```

MCP 镜像 455MB，embedding 镜像 668MB。`compose` 里 tag 走 `KATANA_MCP_TAG`，**刻意不接受
`latest`**——部署要能说清跑的是哪个 commit。

## 迁移（一次性）

```bash
deploy/migrate-data-to-volumes.sh              # 干跑，看清每一步
deploy/migrate-data-to-volumes.sh --apply
```

它做的事：停现有 MCP → 逐域建卷 → root 身份 `cp -a` 并 `chown` 到 10001 → **以运行用户身份**在卷内跑 `git fsck` 并比对 HEAD/tree 与源逐字一致 → 宿主目录**改名**为 `.pre-container`。

三条纪律：

1. **不删宿主目录，只改名。** 容器稳定跑满一周后由人删。
2. **脏仓拒迁。** 迁一个脏仓等于把脏状态一起搬进卷（`--force` 可显式接受）。
3. **不信任 `cp`。** 每域迁完立刻 fsck + hash 比对，任一不符即中止且不动宿主目录。

## 启动

```bash
KATANA_MCP_TAG=$REV docker compose -f deploy/docker-compose.yml up -d
```

端口与全部 `KATANA_*` env 与容器化前**逐字一致**，client 零改动——MCP 本来就是 HTTP 接口，这正是这件事能低成本做成的原因。

确认三个容器健康后，再 `systemctl --user disable` 掉旧的 user unit。

## 备份

```bash
deploy/backup-volumes.sh
```

卷只读挂进一次性容器，往宿主 `/data/backups/katana-data/*.git` 做 bare mirror（拉模式，**不往受治理的卷写任何东西**）。

两个刻意的选择：

- **mirror 落宿主，不落卷。** 备份要能在 Docker 挂了、卷损坏、dockerd 起不来时拿得到。把备份也锁进 Docker 等于没备份。
- **容器以调用者身份跑，不是 root。** 否则 mirror 文件归 root，备份就只有靠 Docker 才动得了。（卷内容是 0755/0644 的 10001 属主，uid 1000 只读挂载读得到，已实测。）

⚠️ 本机 mirror 只防误删与仓损坏，**不防磁盘故障**——它和 Docker 数据在同一块盘上。异地副本是独立的一件事。

### 🔴 备份对源侧 ref 权限敏感 —— 迁移上线当天会断，演练提前逼出来了

演练里 work-records 备份失败，git 的报错是：

```
fatal: 'refs/tags/pre-flatten-baseline-20260729T084648Z-902583' has a null OID
```

**这句话会骗人。** 那个 tag 完好无损：`cat-file -t` 是 `tag`，指向
`1c44ff0651f2550cb49e74e5b023221f80984772`，`fsck --connectivity-only` 无输出。
真因是**文件权限**：

```
$ ls -l /data/work-records/.git/refs/tags/
-rw-rw-r-- pre-flat-migration-20260729T140829Z          # 0664
-rw------- pre-flatten-baseline-20260729T084648Z-902583  # 0600 ← 只有这一个
```

git 读不到一个 ref 时，报出来的形状就是 `has a null OID`。所以**「坏 tag」是误诊，
按那个方向去删/重建 tag 等于对着错误目标动生产数据。**

为什么宿主上没暴露、演练里暴露了：宿主上该文件属主是 uid 1000，`backup-volumes.sh`
也以 uid 1000 跑，读得到，生产 mirror 一直是好的（`/data/backups/katana-data/work-records.git`，
3020 commits）。而演练种卷时按真迁移做了 `chown -R 10001:10001`，0600 于是对 uid 1000
不可读 —— **`migrate-data-to-volumes.sh` 迁移时做的是同一个 chown**，所以这不是演练
特有的问题，是**真迁移上线后生产备份会以完全相同的方式断掉**，而且断了没人会发现。

修法不是去动那个 0600 文件（生产面只读），而是让备份路径对源侧权限不敏感：
读源改走 root（源始终 `:ro` 挂载，root 也写不进去），产物在同一次容器调用里
`chown` 回调用者，`README` 里「备份不能归 root」那条纪律照旧成立。
另加一条完备性判据：mirror 的 commit 数与 ref 集合必须与源逐字相等 ——
「clone 退出 0」不等于「全都拷过来了」。

两处吞错误的写法也已修（`backup-volumes.sh` 的 `>/dev/null 2>&1` 吃掉 git 的 fatal；
`rehearse.sh` 的 `if <pipeline> | tail -3` 叠 `pipefail` 让整个备份判据段既不 PASS
也不 FAIL 地消失）。演练步骤 6b 用一次性小仓对这两种形态做负例自证：不可读 ref
必须被吃掉，真损坏 ref 必须显式 FAIL 并带 git 原文。

## 回滚

```bash
KATANA_MCP_TAG=$REV docker compose -f deploy/docker-compose.yml down
deploy/migrate-data-to-volumes.sh --rollback --apply   # 删卷、宿主目录改回
systemctl --user enable --now katana-{wiki,work-folder,memory}-mcp.service
```

## 安全加固（compose 里已启用）

`read_only: true` 只读 rootfs + `/tmp` tmpfs、`cap_drop: [ALL]`、`no-new-privileges`、端口只绑 `127.0.0.1`、日志轮转。

git 配置写在镜像的 `/etc/gitconfig` 而不是 `$HOME/.gitconfig`——只读 rootfs 下没有可写的 HOME。

## 上线前演练

```bash
deploy/rehearse.sh          # 跑完自动拆
deploy/rehearse.sh --keep   # 留栈手工继续戳（端口 15601/15602/15605）
deploy/rehearse.sh --down   # 只拆
```

用**真数据、真容器、真 MCP 往返**做一次完整彩排：从生产目录 `cp -a`（只读挂载，与真迁移逐字同款）种 staging 卷 → 起栈 → 三域 MCP 工具面往返 → 校验写入真落进卷内 git 且 author 正确 → 只读 rootfs 加固三项 → 备份能从卷做出 mirror → 拆干净。

与生产的唯一差异是卷名、宿主端口、容器名；镜像、env、加固项、command 全部逐字相同。

**镜像不必事先建。** 步骤 0 发现缺 `katana-mcp:<HEAD>` / `katana-embedding:<HEAD>` 时，
按上面「构建」那两条命令**就地建**。默认 tag 跟 HEAD，每出一个新 commit 就要重建一次，
一份要求先手工建两个镜像才能跑的 ops 工装等于默认不可跑。要严格校验「镜像必须事先
备好」用 `--no-build`。

**当前结果（`67b8431`）：42 PASS / 2 FAIL / 0 SKIP。**

仅剩的两条 FAIL 是 `wiki wiki_search` 与 `work-folder wf_search`，**都是 P0 未接线的
预期 FAIL**：两个 server 仍调宿主 vault_search（见下方待决项），容器里打不到宿主
loopback，报 `Connection refused`。dd 单 `dev_katana_search_wiring_01` 正在修这一条；
它转绿之后本演练即全绿，**没有其它已知阻塞项**。

### 判据 11「全绿」的口径

**全绿 = `FAIL=0` 且 `SKIP=0`。** SKIP 的语义是「前置塌了，这条判据根本没跑」——
它把连锁塌方从十几条 FAIL 收敛成一条 FAIL + 若干 SKIP，那是为了**可读**，不是为了
好看。没跑过的判据不能算通过，否则「把判据 SKIP 掉」就成了让读数变绿最省事的办法。
脚本的退出码对 FAIL 与 SKIP 一视同仁，结果行也会直接印出这个口径。

### 读数与生产状态解耦：两根轴，都要挡

生产 `/data/work-records` 是活仓（实测约 **70+ 笔 commit/小时**），会以两种方式污染读数：

| 轴 | 症状 | 挡法 |
|---|---|---|
| **脏工作区** | `cp -a` 把未跟踪文件一并搬进卷 → `DirtyWorkTreeError` | 副本内按迁移纪律洗净（步骤 1） |
| **撕裂副本** | `cp -a` 不是原子快照，objects/ 与 refs/ 在不同瞬间被抄 → 卷里 HEAD 指向没抄进来的 tree → `error: bad tree object HEAD` | 抄完验自洽（`vol_intact`），不自洽就整卷重抄，最多 `KATANA_SEED_ATTEMPTS`（默认 4）次 |

只挡第一根轴是不够的，而且第二根轴**会伪装成第一根轴的成功**：撕裂副本上
`git status` 非零退出、stdout 为空，`... | grep -c .` 把它数成 **0 条**，于是
「卷内仓干净」PASS、「洗净 0→0」、`post_scrub=0/0/0`、✅「与生产脏度无关」四条断言
一起说谎，而真实情况是 work-folder 容器根本起不来。**一个断言读不出被测量时必须
报错，不能返回一个恰好等于「健康」的值**——现在 `vol_dirty_count` 回显 `ERR`，
按 FAIL 处理。

结果行直接印解耦证据：

```
  读数解耦证据：
    seed_mode=scrub  seed_dirty=116（work-folder）   ← 环境噪声，每次都不同
    scrub=wiki:0→0 work-folder:116→0 memory:0→0
    post_scrub=0/0/0  ← 这一段两次运行相同则读数可比
```

`seed_dirty` 是这一刻生产的脏度，`post_scrub` 是**真正喂给容器栈的输入**。只要
`post_scrub` 恒为 `0/0/0`，两次不同脏度的运行就可以直接比 PASS/FAIL 计数。

实测三轮连跑（生产全程在落账）：

| 轮 | seed_dirty | 种卷 | PASS/FAIL/SKIP |
|---|---|---|---|
| A | 108 | 一次抄成 | 42 / 2 / 0 |
| B | 111 | 一次抄成 | 42 / 2 / 0 |
| C（`KATANA_SEED_FORCE_TEAR=2` 故障注入） | 116 | 三域各重抄 2 次 | 42 / 2 / 0 |

C 用故障注入把撕裂强行造出来，证明重抄这条路真的会跑而不是「恰好没撞上」——
真实撕裂是概率事件（落账约 1 笔/分钟，抄一次十几秒），连跑两次都没撞上不等于免疫。

对照：改造前同一份代码在生产净时读 25/2、脏时读 14/13，撞上撕裂时读 29/3/6，
唯一变量就是种卷那一刻的生产状态——那种读数没有意义。

### ⚠️ 待决：检索后端在容器内不可达

`wiki_search` / `wf_search` 要打宿主上的 vault-search，而它**只监听 `127.0.0.1:18082`**。容器经 `host.docker.internal` 出来落在 bridge 网关（本机 `192.168.228.1`），打不到宿主 loopback —— `ConnectTimeout`。

代码侧已经改好（`vault_search.DEFAULT_BASE_URL` 现可经 `KATANA_VAULT_SEARCH_URL` 覆盖，默认值不变、宿主部署零影响），compose 也已配好 `extra_hosts` 与该 env。**缺的只是让 vault-search 在 bridge 网关上也监听一份。** 三条路：

| 方案 | 代价 |
|---|---|
| vault-search 增监听 bridge 网关 IP | 改一个宿主服务的 bind，最小；注意别图省事绑 `0.0.0.0`（本机有 tailnet） |
| wiki/work-folder 改 `network_mode: host` | 零改动别的服务，但放弃端口发布与网络命名空间；**数据面隔离不受影响**（那才是本工程的目标） |
| vault-search 一并容器化，进同一 compose 网络 | 最干净，范围最大 |

三域的**写路径全部已验证可用**，只有检索读路径受此影响。

## 施工中实测踩到的三个坑（都已修，留档免得再犯）

1. **`printf '%s'` 不展开 `\t`** —— 手写 `/etc/gitconfig` 写出了字面反斜杠加 t，`fatal: bad config line`，整个镜像里 git 不可用。改用 `git config --file` 让 git 自己写 git 的配置（自校验）。
2. **helper 容器继承镜像的 `USER katana`** —— 新建卷属主是 root，搬运时 `cp: Permission denied` 且 chown 也做不了。搬运必须 `--user 0:0`；校验则刻意用运行用户身份，顺带证明 chown 之后 MCP 真读得到。
3. **YAML merge key 不做深合并** —— `x-katana-common` 里的 `environment` 会被各服务自己的 `environment:` 整块替换，三个服务的 `GIT_*` 全丢（而 git 会静默回落到镜像默认值，不报错）。改用 map 级 anchor 在 `environment` 内部合并。

4. **`.katana/runtime/` 是 gitignored 的** —— 演练最初用 bare mirror 克隆种卷，结果 work-folder crash-loop：`MutationBrokenError: runtime mutation ledger is incomplete for Git history`。ledger（`mutations.sqlite`）与 manifests 不进 git，克隆自然带不出来。**顺带暴露备份的真缺口：只有 git mirror 恢复不出一个能启动的系统**，故 `backup-volumes.sh` 已加 runtime 态捕获（sqlite 走 backup API 做一致性快照，不是裸 cp）。
5. **compose 对 `ports` 是追加合并不是替换** —— staging override 写了 `1560x` 却仍同时绑 `560x`，直接跟生产实例抢端口起不来。要用 `!override` 标签。
6. **`docker run` 不加 `-i` 收不到 stdin** —— 经 heredoc 喂进去的探针脚本压根没执行，表现是**静默零输出零报错**，极易误判成「MCP 挂了」。
7. **memory MCP 是多租户，挂在 `/t/<tenant>/mcp`** —— 探针打根 `/mcp` 拿 404，客户端表现为 `Session terminated`，又一个看着像崩溃其实是路径写错。

全都是「不实测就发现不了、上生产才炸」的类型。
