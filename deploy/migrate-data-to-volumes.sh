#!/usr/bin/env bash
# 把三域 data root 从宿主目录迁进 named volume。
#
# 迁完之后宿主上就不再存在这三个路径 —— 这正是目的：`--outdir` 手滑、顺手
# `git commit` 这两类事故结构性不可能发生，而不是「写的时候报错」。
# 见 docs/constitution/002-data-plane-privacy.md 第一条。
#
# 纪律：
#   - **不删宿主目录，只改名**（加 .pre-container 后缀）。容器跑满一周再由人删。
#   - 每个域迁完立刻校验：git fsck + HEAD tree hash 与源逐字比对。任一不符即中止。
#   - 幂等：卷已存在且非空则跳过（除非 --force）。
#
# 用法：
#   deploy/migrate-data-to-volumes.sh            # dry-run，打印将做什么
#   deploy/migrate-data-to-volumes.sh --apply
#   deploy/migrate-data-to-volumes.sh --rollback --apply   # 卷删掉、宿主目录改回
set -uo pipefail

# 镜像里的运行用户 uid/gid（mcp/Dockerfile 里固定为 10001），卷内容必须归它，
# 否则容器首次写入 EACCES。
RUN_UID=10001
RUN_GID=10001
# 只用来搬运/校验的一次性容器镜像。用 katana-mcp 自己：它带 git，且已在本机。
HELPER_IMAGE="${KATANA_HELPER_IMAGE:-katana-mcp:latest}"

declare -A DOMAINS=(
  [work-records]=/data/work-records
  [wiki]=/data/wiki
  [memory]=/data/memory
)

APPLY=0; ROLLBACK=0; FORCE=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --rollback) ROLLBACK=1 ;;
    --force) FORCE=1 ;;
    *) echo "用法：$0 [--apply] [--rollback] [--force]" >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
run() { if [ "$APPLY" -eq 1 ]; then "$@"; else printf '  [dry-run] %s\n' "$*"; fi; }

# 在容器里跑一条命令，卷挂 /dst，宿主源目录（若给）只读挂 /src。
#
# $1 = 以哪个身份跑。**搬运必须用 root**：新建的 named volume 属主是 root，而镜像
# 默认 USER 是 katana(10001)，直接跑会 `cp: Permission denied` 且 chown 也做不了。
# 这个坑在一次性副本上实测踩到过——真拿 728M 数据跑会半路失败。
# **校验则刻意用运行用户身份**：既验内容，也顺带证明 chown 之后 MCP 真读得到。
in_helper() {
  local as="$1" vol="$2" src="${3:-}"; shift 3
  if [ -n "$src" ]; then
    docker run --rm --user "$as" -v "$vol:/dst" -v "$src:/src:ro" --entrypoint sh "$HELPER_IMAGE" -c "$*"
  else
    docker run --rm --user "$as" -v "$vol:/dst" --entrypoint sh "$HELPER_IMAGE" -c "$*"
  fi
}

rollback() {
  say "=== rollback：删卷、宿主目录改回 ==="
  for name in "${!DOMAINS[@]}"; do
    local host="${DOMAINS[$name]}"
    if [ -d "$host.pre-container" ]; then
      run sudo mv "$host.pre-container" "$host"
      say "  $name: 宿主目录已恢复"
    else
      say "  $name: 无 .pre-container 备份，跳过"
    fi
    run docker volume rm "katana-$name"
  done
  say "回滚完成。"
}

migrate() {
  say "=== 迁移三域 data root 进 named volume ==="
  [ "$APPLY" -eq 1 ] || say "（dry-run；加 --apply 才真执行）"
  say

  if ! docker image inspect "$HELPER_IMAGE" >/dev/null 2>&1; then
    say "FATAL: 找不到镜像 $HELPER_IMAGE，先 docker build -f mcp/Dockerfile ."; exit 2
  fi

  # 先整体停服，避免迁移期间还有 mutation 写进旧目录造成源/卷分叉
  say "[0] 停掉现有 MCP 服务（迁移期间不能有写入）"
  run systemctl --user stop katana-wiki-mcp.service katana-work-folder-mcp.service katana-memory-mcp.service
  say

  for name in "${!DOMAINS[@]}"; do
    local host="${DOMAINS[$name]}" vol="katana-$name"
    say "── $name ──"

    if [ ! -d "$host/.git" ]; then say "  跳过：$host 不是 git 仓（可能已迁）"; say; continue; fi

    local dirty; dirty=$(git -C "$host" status --porcelain 2>/dev/null | wc -l)
    if [ "$dirty" -ne 0 ] && [ "$FORCE" -eq 0 ]; then
      say "  FAIL：$host 有 $dirty 条未提交改动。迁移一个脏仓等于把脏状态一起搬进卷。"
      say "        先让写者收干净（见 002 第二条），或 --force 明确接受。"
      exit 1
    fi

    if docker volume inspect "$vol" >/dev/null 2>&1 && [ "$FORCE" -eq 0 ]; then
      say "  跳过：卷 $vol 已存在（--force 可覆盖）"; say; continue
    fi

    local src_head src_tree
    src_head=$(git -C "$host" rev-parse HEAD)
    src_tree=$(git -C "$host" rev-parse 'HEAD^{tree}')
    say "  源 HEAD=${src_head:0:8} tree=${src_tree:0:8}"

    run docker volume create "$vol"
    # cp -a 保权限与符号链接；. 结尾避免多套一层目录
    run in_helper 0:0 "$vol" "$host" "cp -a /src/. /dst/ && chown -R $RUN_UID:$RUN_GID /dst"

    # 校验：不信任 cp，实测仓完整性与内容一致性
    if [ "$APPLY" -eq 1 ]; then
      local out
      out=$(in_helper "$RUN_UID:$RUN_GID" "$vol" "" "git -C /dst fsck --no-progress --no-dangling >/dev/null 2>&1 && git -C /dst rev-parse HEAD && git -C /dst rev-parse 'HEAD^{tree}'" 2>&1)
      local vol_head vol_tree
      vol_head=$(echo "$out" | sed -n 1p); vol_tree=$(echo "$out" | sed -n 2p)
      if [ "$vol_head" = "$src_head" ] && [ "$vol_tree" = "$src_tree" ]; then
        say "  OK 卷内 fsck 通过，HEAD 与 tree 与源逐字一致"
      else
        say "  FAIL 校验不符：卷 HEAD=${vol_head:-?} tree=${vol_tree:-?}"
        say "       卷保留供排查，宿主目录未动。中止。"
        exit 1
      fi
    else
      say "  [dry-run] 会在卷内跑 git fsck 并比对 HEAD/tree"
    fi

    # 安全网：只改名，不删。容器稳定跑一周后由人删。
    run sudo mv "$host" "$host.pre-container"
    say "  宿主目录已改名为 $host.pre-container（**不删**，稳定后由人清理）"
    say
  done

  say "迁移完成。下一步：docker compose -f deploy/docker-compose.yml up -d"
  say "旧的 user unit 已停；确认容器健康后再 systemctl --user disable 它们。"
}

if [ "$ROLLBACK" -eq 1 ]; then rollback; else migrate; fi
