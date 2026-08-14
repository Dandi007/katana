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
# 备份容器**以调用者身份**跑，不是 root——否则写出的 mirror 归 root，备份就只有
# 靠 Docker 才动得了，而把备份也锁进 Docker 等于没备份。实测：卷内容是
# 0755/0644 的 10001 属主，uid 1000 只读挂载读得到，够用。
AS_USER="$(id -u):$(id -g)"
IMAGE="${KATANA_HELPER_IMAGE:-katana-mcp:latest}"
DOMAINS=(work-records wiki memory)

rc=0
for name in "${DOMAINS[@]}"; do
  vol="katana-$name"
  mirror="$MIRROR_ROOT/$name.git"

  if ! docker volume inspect "$vol" >/dev/null 2>&1; then
    echo "SKIP $name：卷 $vol 不存在（尚未迁移？）" >&2
    continue
  fi
  if [ ! -d "$mirror" ]; then
    # 首次：从卷里克隆出 bare mirror
    echo "INIT $name：首次建 mirror"
    if ! docker run --rm --user "$AS_USER" \
        -v "$vol:/src:ro" -v "$MIRROR_ROOT:/mirror" \
        --entrypoint sh "$IMAGE" -c "git clone --mirror /src /mirror/$name.git" >/dev/null 2>&1; then
      echo "FAIL $name：初始 clone 失败" >&2; rc=1; continue
    fi
  else
    if ! docker run --rm --user "$AS_USER" \
        -v "$vol:/src:ro" -v "$MIRROR_ROOT:/mirror" \
        --entrypoint sh "$IMAGE" -c "git --git-dir=/mirror/$name.git fetch --prune /src '+refs/*:refs/*'" >/dev/null 2>&1; then
      echo "FAIL $name：fetch 失败" >&2; rc=1; continue
    fi
  fi

  # runtime 态（gitignored，不进 mirror，但恢复时必需）
  rtdir="$MIRROR_ROOT/$name-runtime"
  mkdir -p "$rtdir"
  if ! docker run --rm --user "$AS_USER" \
      -v "$vol:/src:ro" -v "$rtdir:/out" \
      --entrypoint python "$IMAGE" -c '
import os, shutil, sqlite3, sys
src, out = "/src/.katana/runtime", "/out"
if not os.path.isdir(src):
    sys.exit(0)                      # 该域没有 runtime 态，正常
db = os.path.join(src, "mutations.sqlite")
if os.path.exists(db):
    # 一致性快照：sqlite backup API 会与并发写者协调，裸 cp 不会
    s = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
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

  # 报进度而不是盲目说成功：比对卷与 mirror 的 HEAD
  src=$(docker run --rm -v "$vol:/src:ro" --entrypoint sh "$IMAGE" -c "git -C /src rev-parse HEAD" 2>/dev/null)
  dst=$(git --git-dir="$mirror" rev-parse HEAD 2>/dev/null)
  if [ "$src" = "$dst" ]; then
    echo "OK   $name: ${dst:0:8}"
  else
    # 源在 fetch 之后又前进一步是正常的（工作线持续落账），只记录不算失败
    echo "LAG  $name: src=${src:0:8} mirror=${dst:0:8}"
  fi
done
exit $rc
