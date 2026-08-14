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
