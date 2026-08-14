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

## 构建

```bash
cd <repo>
REV=$(git rev-parse --short HEAD)
docker build -f mcp/Dockerfile --build-arg GIT_REVISION=$REV -t katana-mcp:$REV .
```

镜像 455MB。`compose` 里 tag 走 `KATANA_MCP_TAG`，**刻意不接受 `latest`**——部署要能说清跑的是哪个 commit。

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

**当前结果：22 PASS / 2 FAIL**，唯一 FAIL 是下面这条待决项。

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
