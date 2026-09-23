#!/usr/bin/env bash
# 三域 data volume → 宿主 bare mirror。
#
# 迁进 named volume 之后，宿主上不再有 /data/<domain> 路径，原来直接读宿主目录的
# mirror job 会失效——本脚本是它的容器化替代：把卷**只读**挂进一次性容器，从容器
# 里往挂进来的 mirror 目录 fetch。
#
# 拉模式：mirror 去 fetch 源，**不往受治理的卷写任何东西**。受治理的仓保持零外部
# 改动，也就不会撞 governed clean-tree 前置条件。
#
# mirror 落在宿主（/data/backups/katana-data）是刻意的：备份要能在 Docker 挂了、
# 卷损坏、甚至 dockerd 起不来的时候拿得到。把备份也锁进 Docker 等于没备份。
#
# **只有 git mirror 不构成完整恢复路径**（真机演练发现）：`.katana/runtime/`
# （mutations.sqlite + manifests）是 gitignored 的，不进 mirror；只用 mirror 恢复出来
# 的仓，kernel 启动时会发现 ledger 空而 git 历史含 receipt commit，直接
# MutationBrokenError 拒绝启动。所以每轮另抓一份 runtime 态。
#
# sqlite 用 Python 的 backup API 做**一致性**快照，不是裸 cp/tar —— 备份期间服务仍在
# 写，裸拷会拿到撕裂的库，而那种损坏只在恢复时才发作。
set -uo pipefail

MIRROR_ROOT="${KATANA_MIRROR_ROOT:-/data/backups/katana-data}"
# 产物必须归**调用者**，不能归 root——否则备份就只有靠 Docker 才动得了，而把备份
# 也锁进 Docker 等于没备份。这条纪律不变。
AS_USER="$(id -u):$(id -g)"
#
# 但**读源**不能以调用者身份跑。原实现 `--user $AS_USER` 读卷，隐含假设「卷里每个
# 文件都对 uid 1000 可读」，那个假设是错的：
#   /data/work-records/.git/refs/tags/pre-flatten-baseline-20260729T084648Z-902583
#   是 0600（同目录另一个 tag 是 0664），迁进卷后 chown 成 10001，uid 1000 读不到。
# git 把「读不到的 ref」报成 `has a null OID`，看起来像仓损坏，其实是权限。
# 实测：该 tag 对象本身完好（cat-file -t = tag，fsck --connectivity-only 无输出），
# 以属主身份 clone --mirror 成功。
#
# 这不是只影响演练：`migrate-data-to-volumes.sh` 迁移时就会 `chown -R 10001:10001`，
# 所以**真迁移上线后，生产的 backup 会以同样方式断掉**——演练提前把它逼出来了。
# 故读源一律 root（`:ro` 挂载，root 也写不进源），产物在同一次容器调用里 chown 回
# 调用者。两个要求同时满足：对源侧权限不敏感 + 产物不留 root 残留。
HELPER_READ_USER=0:0
IMAGE="${KATANA_HELPER_IMAGE:-katana-mcp:latest}"
# 域列表可被 harness 覆盖（rehearse.sh 的负例自证要单域跑）。默认三域不变。
read -r -a DOMAINS <<< "${KATANA_BACKUP_DOMAINS:-work-records wiki memory}"

rc=0
for name in "${DOMAINS[@]}"; do
  vol="katana-$name"
  mirror="$MIRROR_ROOT/$name.git"

  if ! docker volume inspect "$vol" >/dev/null 2>&1; then
    echo "SKIP $name：卷 $vol 不存在（尚未迁移？）" >&2
    continue
  fi
  # `>/dev/null 2>&1` 曾把 git 的 fatal 一起吞掉，只剩一句没有原因的「初始 clone 失败」。
  # 备份工具尤其不能这么写：它的失败本来就没人盯，再把原因藏起来，就是「以为有备份」。
  # 现在 git 的 fatal/error 原样打出来。
  if [ ! -d "$mirror" ]; then
    # 首次：从卷里克隆出 bare mirror
    echo "INIT $name：首次建 mirror"
    if ! out=$(docker run --rm --user "$HELPER_READ_USER" \
        -v "$vol:/src:ro" -v "$MIRROR_ROOT:/mirror" \
        --entrypoint sh "$IMAGE" -c \
        "git clone --mirror /src /mirror/$name.git && chown -R $AS_USER /mirror/$name.git" 2>&1); then
      echo "FAIL $name：初始 clone 失败" >&2
      echo "$out" | grep -iE 'fatal|error|warning' | tail -3 | sed 's/^/     git: /' >&2
      rc=1; continue
    fi
  else
    if ! out=$(docker run --rm --user "$HELPER_READ_USER" \
        -v "$vol:/src:ro" -v "$MIRROR_ROOT:/mirror" \
        --entrypoint sh "$IMAGE" -c \
        "git --git-dir=/mirror/$name.git fetch --prune /src '+refs/*:refs/*' && chown -R $AS_USER /mirror/$name.git" 2>&1); then
      echo "FAIL $name：fetch 失败" >&2
      echo "$out" | grep -iE 'fatal|error|warning' | tail -3 | sed 's/^/     git: /' >&2
      rc=1; continue
    fi
  fi

  # 完备性判据：mirror 的 ref 数与 commit 数必须与源逐字相等。
  # 「clone 退出 0」不等于「全都拷过来了」——不可读的 ref、被 prune 掉的分支都能让
  # 一个成功的 clone 产出一份**残缺**的 mirror，而那种残缺只在恢复那天才发作。
  s_refs=$(docker run --rm --user "$HELPER_READ_USER" -v "$vol:/src:ro" --entrypoint sh "$IMAGE" \
             -c "git -C /src for-each-ref --format='%(refname)' | sort | md5sum | cut -d' ' -f1" 2>/dev/null)
  s_cnt=$(docker run --rm --user "$HELPER_READ_USER" -v "$vol:/src:ro" --entrypoint sh "$IMAGE" \
             -c "git -C /src rev-list --count HEAD" 2>/dev/null)
  m_refs=$(git --git-dir="$mirror" for-each-ref --format='%(refname)' 2>/dev/null | sort | md5sum | cut -d' ' -f1)
  m_cnt=$(git --git-dir="$mirror" rev-list --count HEAD 2>/dev/null)
  if [ "$s_cnt" != "$m_cnt" ]; then
    echo "FAIL $name：commit 数不符 源=$s_cnt mirror=$m_cnt —— mirror 残缺" >&2; rc=1
  elif [ "$s_refs" != "$m_refs" ]; then
    echo "FAIL $name：ref 集合不符（源与 mirror 的 refname 列表不一致）—— mirror 残缺" >&2; rc=1
  else
    echo "FULL $name：commit=$s_cnt，ref 集合与源逐字一致"
  fi

  # runtime 态（gitignored，不进 mirror，但恢复时必需）
  # 同样以 root 读源（runtime 态里 mutations.sqlite 的属主/模式同样不该被假设），
  # 产物在下一条 chown 回调用者。
  rtdir="$MIRROR_ROOT/$name-runtime"
  mkdir -p "$rtdir"
  if ! docker run --rm --user "$HELPER_READ_USER" \
      -v "$vol:/src:ro" -v "$rtdir:/out" \
      --entrypoint python "$IMAGE" -c '
import os, shutil, sqlite3, sys, tempfile
src, out = "/src/.katana/runtime", "/out"
if not os.path.isdir(src):
    sys.exit(0)                      # 该域没有 runtime 态，正常
db = os.path.join(src, "mutations.sqlite")
if os.path.exists(db):
    # 源卷是 `:ro`，而 **WAL 模式的 reader 也要建 -shm 文件**，只读挂载上建不出来：
    #   sqlite3.OperationalError: unable to open database file
    # 这条此前一直被 work-records 的 clone 失败盖住，clone 修好才露出来。
    # `immutable=1` 能绕开 WAL/shm，但它向 sqlite 保证「文件不会变」——生产上
    # work-folder MCP 正在并发写同一个库，那个保证是假的，会读出撕裂的页。
    #
    # 故：先把 db 连同 -wal/-shm 一起拷进容器内的可写临时目录，再以**读写**方式打开
    # 那份副本让 sqlite 自己回放 WAL，最后用 backup API 落一致快照。
    # 受治理的卷全程只读，一个字节都不写。
    with tempfile.TemporaryDirectory() as td:
        tmp = os.path.join(td, "mutations.sqlite")
        shutil.copy2(db, tmp)
        for suffix in ("-wal", "-shm"):
            side = db + suffix
            if os.path.exists(side):
                shutil.copy2(side, tmp + suffix)
        s = sqlite3.connect(tmp)                       # 可写副本：允许回放 WAL
        s.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        d = sqlite3.connect(os.path.join(out, "mutations.sqlite"))
        with d: s.backup(d)
        s.close(); d.close()
man = os.path.join(src, "manifests")
if os.path.isdir(man):
    shutil.rmtree(os.path.join(out, "manifests"), ignore_errors=True)
    shutil.copytree(man, os.path.join(out, "manifests"))
' 2>/dev/null; then
    echo "WARN $name：runtime 态备份失败（mirror 已更新，但恢复需手工修 ledger）" >&2
    rc=1
  fi
  # root 写出来的 runtime 产物归还调用者——否则备份又变成只有 Docker 动得了。
  docker run --rm --user "$HELPER_READ_USER" -v "$rtdir:/out" --entrypoint sh "$IMAGE" \
    -c "chown -R $AS_USER /out" >/dev/null 2>&1

  # 报进度而不是盲目说成功：比对卷与 mirror 的 HEAD
  src=$(docker run --rm --user "$HELPER_READ_USER" -v "$vol:/src:ro" --entrypoint sh "$IMAGE" -c "git -C /src rev-parse HEAD" 2>/dev/null)
  dst=$(git --git-dir="$mirror" rev-parse HEAD 2>/dev/null)
  if [ "$src" = "$dst" ]; then
    echo "OK   $name: ${dst:0:8}"
  else
    # 源在 fetch 之后又前进一步是正常的（工作线持续落账），只记录不算失败
    echo "LAG  $name: src=${src:0:8} mirror=${dst:0:8}"
  fi
done
exit $rc
