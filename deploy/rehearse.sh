#!/usr/bin/env bash
# 容器化上线前的真机演练 —— 用真数据、真容器、真 MCP 往返，全程零触碰生产。
#
# 种子方式与**真迁移逐字相同**：从生产目录 `cp -a`（只读挂载）。
#
# 第一版用 bare mirror 克隆种入，看起来更"零触碰"，但**错了**：`.katana/runtime/`
# （mutations.sqlite + manifests）是 gitignored 的，不在 mirror 里；kernel 启动时
# 发现 ledger 空而 git 历史含 receipt commit，直接 MutationBrokenError 拒绝启动。
# 演练必须复刻真迁移的搬运方式，否则测的不是同一件事。
#
# 生产目录全程只以 `:ro` 挂载，不写不改。
#
# 与生产的唯一差异是卷名、宿主端口、容器名（见 docker-compose.staging.yml）——
# 镜像、env、加固项、command 全部逐字相同，否则演练就不是演练。
#
# 覆盖面：
#   1. 三个容器起得来且健康
#   2. 三域的 MCP 工具面真往返（work-folder 走完整写事务，memory 写，wiki 读）
#   3. 写入真落进卷内 git，且 author 是 katana-mcp
#   4. 只读 rootfs 下 git 事务仍可提交（这是最容易翻车的一项）
#   5. 备份能从卷里做出 mirror
#
# 用法：
#   deploy/rehearse.sh          # 跑完自动拆
#   deploy/rehearse.sh --keep   # 保留 staging 栈供手工继续戳
#   deploy/rehearse.sh --down   # 只拆
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT=katana-staging
MIRROR_ROOT="${KATANA_MIRROR_ROOT:-/data/backups/katana-data}"
STAGING_MIRROR="${KATANA_STAGING_MIRROR:-/data/backups/katana-staging}"
IMAGE_TAG="${KATANA_MCP_TAG:-}"
COMPOSE=(docker compose -p "$PROJECT" -f "$HERE/docker-compose.yml" -f "$HERE/docker-compose.staging.yml")

declare -A PORTS=([wiki]=15601 [work-folder]=15602 [memory]=15605)
declare -A VOLS=([wiki]=katana-staging-wiki [work-folder]=katana-staging-work-records [memory]=katana-staging-memory)
declare -A MIRRORS=([wiki]=wiki [work-folder]=work-records [memory]=memory)
declare -A SRCDIRS=([wiki]=/data/wiki [work-folder]=/data/work-records [memory]=/data/memory)

KEEP=0; DOWN_ONLY=0
for a in "$@"; do
  case "$a" in
    --keep) KEEP=1 ;;
    --down) DOWN_ONLY=1 ;;
    *) echo "用法：$0 [--keep|--down]" >&2; exit 2 ;;
  esac
done

pass=0; fail=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
step() { printf '\n=== %s ===\n' "$*"; }

teardown() {
  step "拆除 staging"
  KATANA_MCP_TAG="${IMAGE_TAG:-x}" "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1
  for v in "${VOLS[@]}"; do docker volume rm "$v" >/dev/null 2>&1; done
  rm -rf "$STAGING_MIRROR"
  echo "  staging 卷与 mirror 已清除（生产未触碰）"
}

if [ "$DOWN_ONLY" -eq 1 ]; then teardown; exit 0; fi

# ---------------------------------------------------------------------------
step "0 前置"
if [ -z "$IMAGE_TAG" ]; then
  IMAGE_TAG="$(git -C "$HERE/.." rev-parse --short HEAD 2>/dev/null)"
fi
export KATANA_MCP_TAG="$IMAGE_TAG"
if docker image inspect "katana-mcp:$IMAGE_TAG" >/dev/null 2>&1; then
  ok "镜像 katana-mcp:$IMAGE_TAG 在位"
else
  bad "缺镜像 katana-mcp:$IMAGE_TAG —— 先 docker build -f mcp/Dockerfile --build-arg GIT_REVISION=$IMAGE_TAG -t katana-mcp:$IMAGE_TAG ."
  exit 1
fi
for d in "${!SRCDIRS[@]}"; do
  [ -d "${SRCDIRS[$d]}/.git" ] && ok "生产源 ${SRCDIRS[$d]} 在位（只读使用）" || { bad "缺源 ${SRCDIRS[$d]}"; exit 1; }
done

# ---------------------------------------------------------------------------
step "1 从生产目录 cp -a 种 staging 卷（只读挂载，与真迁移同款）"
teardown >/dev/null 2>&1
for d in "${!VOLS[@]}"; do
  vol="${VOLS[$d]}"; src="${SRCDIRS[$d]}"
  docker volume create "$vol" >/dev/null
  # 与 migrate-data-to-volumes.sh 同款：root 身份 cp -a 全量（含 gitignored 的
  # .katana/runtime），再 chown 给镜像的运行用户
  if docker run --rm --user 0:0 -v "$vol:/dst" -v "$src:/src:ro" --entrypoint sh \
       "katana-mcp:$IMAGE_TAG" -c "cp -a /src/. /dst/ && chown -R 10001:10001 /dst" >/dev/null 2>&1; then
    n=$(docker run --rm --user 10001:10001 -v "$vol:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" -c "git -C /d rev-list --count HEAD" 2>/dev/null)
    ok "$d 卷已种入（$n commits）"
  else
    bad "$d 卷种入失败"; exit 1
  fi
done

# ---------------------------------------------------------------------------
step "2 起 staging 栈"
# 不吞错误：失败时把 compose 的真实输出打出来。吞掉 stderr 的代价实测过——
# 第一次演练卡在「compose up 失败」四个字上，什么都看不见。
up_out=$("${COMPOSE[@]}" up -d 2>&1); up_rc=$?
if [ "$up_rc" -eq 0 ]; then
  ok "compose up"
else
  bad "compose up 失败（rc=$up_rc）"
  echo "$up_out" | sed 's/^/       /'
  exit 1
fi

for i in $(seq 1 30); do
  healthy=$("${COMPOSE[@]}" ps --format json 2>/dev/null | grep -c '"Health":"healthy"')
  [ "${healthy:-0}" -ge 3 ] && break
  sleep 2
done
if [ "${healthy:-0}" -ge 3 ]; then ok "三个容器 healthy"; else bad "健康检查未全绿（healthy=$healthy）"; "${COMPOSE[@]}" ps; fi

for d in "${!PORTS[@]}"; do
  path="/mcp"; [ "$d" = "memory" ] && path="/t/uther/mcp"   # memory 多租户挂载
  if curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORTS[$d]}$path" | grep -qE '^[24]'; then
    ok "$d 端口 ${PORTS[$d]} 有响应"
  else
    bad "$d 端口 ${PORTS[$d]} 无响应"
  fi
done

# ---------------------------------------------------------------------------
step "3 真 MCP 往返（用镜像自带的 fastmcp.Client）"
# docker run 必须带 -i：脚本经 stdin 喂给容器内的 python，不加 -i 容器收不到 stdin，
# 表现是**静默零输出零报错**，很容易误判成「MCP 挂了」。
RESULT=$(docker run --rm -i --network host --entrypoint python "katana-mcp:$IMAGE_TAG" - <<'PY' 2>&1
import asyncio, json
from fastmcp import Client

# 每域独立兜异常：一个域炸掉不该让另外两个域的结果一起消失。第一版没兜，
# wf_search 的异常把整个 main 带挂，memory/wiki 显示成"失败"其实根本没跑到。
async def domain(out, key, fn):
    try:
        await fn()
    except Exception as e:
        out[key + "_err"] = f"{type(e).__name__}: {e}"[:180]

async def main():
    out = {}

    async def wf():
        async with Client("http://127.0.0.1:15602/mcp") as c:
            out["wf_tools"] = len(await c.list_tools())
            r = await c.call_tool("wf_create", {"topic": "容器化演练 rehearsal"})
            d = json.loads(r.content[0].text) if r.content else {}
            fid = d.get("folder_id")
            out["wf_create"] = bool(d.get("created")) and bool(fid)
            r = await c.call_tool("fs_create", {"folder_id": fid, "filename": "probe.md",
                                                "content": "# 演练写入\n\n容器内 governed 事务。\n"})
            d2 = json.loads(r.content[0].text) if r.content else {}
            out["fs_create"] = bool(d2.get("ok")) and bool(d2.get("commit"))
            r = await c.call_tool("wf_search", {"query": "容器化演练", "top_k": 3})
            out["wf_search"] = True

    async def mem():
        # memory 是多租户：每个 tenant 挂在 /t/<tenant>/mcp，不是根 /mcp（探针写成 /mcp
        # 会拿到 404，客户端表现为 "Session terminated"，很容易误判成服务崩了）。
        async with Client("http://127.0.0.1:15605/t/uther/mcp") as c:
            out["mem_tools"] = len(await c.list_tools())
            r = await c.call_tool("memory_create", {
                "name": "container-rehearsal-probe",
                "description": "容器化演练写入探针（可删）",
                "body": "## Fact\n\n容器内 memory 域 governed 写事务演练。\n\n## How to Verify\n\n```bash\ndocker volume inspect katana-staging-memory\n```\n",
                "type": "reference"})
            d = json.loads(r.content[0].text) if r.content else {}
            out["mem_create"] = bool(d.get("id") or d.get("ok"))

    async def wiki():
        async with Client("http://127.0.0.1:15601/mcp") as c:
            out["wiki_tools"] = len(await c.list_tools())
            r = await c.call_tool("wiki_search", {"query": "agent"})
            out["wiki_search"] = bool(r.content)

    await domain(out, "wf", wf)
    await domain(out, "mem", mem)
    await domain(out, "wiki", wiki)
    print(json.dumps(out, ensure_ascii=False))

asyncio.run(main())
PY
)
echo "$RESULT" | tail -3
J=$(echo "$RESULT" | tail -1)
chk() { echo "$J" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('$1') else 1)" 2>/dev/null; }
chk wf_create   && ok "work-folder wf_create 成功"        || bad "work-folder wf_create 失败"
chk fs_create   && ok "work-folder fs_create 提交成功"     || bad "work-folder fs_create 失败"
chk mem_create  && ok "memory memory_create 成功"          || bad "memory memory_create 失败"
chk wiki_search && ok "wiki wiki_search 成功"              || bad "wiki wiki_search 失败"
chk wf_search   && ok "work-folder wf_search 成功"        || bad "work-folder wf_search 失败"

# ---------------------------------------------------------------------------
step "4 写入真落进卷内 git，且 author 正确"
for d in work-folder memory; do
  vol="${VOLS[$d]}"
  info=$(docker run --rm --user 10001:10001 -v "$vol:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" \
        -c "git -C /d log -1 --format='%an|%s' && git -C /d status --porcelain | wc -l" 2>/dev/null)
  author=$(echo "$info" | sed -n 1p | cut -d'|' -f1)
  subject=$(echo "$info" | sed -n 1p | cut -d'|' -f2-)
  dirty=$(echo "$info" | sed -n 2p)
  [ "$author" = "katana-mcp" ] && ok "$d 最新 commit author=katana-mcp（$subject）" || bad "$d author='$author'，期望 katana-mcp"
  [ "$dirty" = "0" ] && ok "$d 事务后仓干净" || bad "$d 事务后仍有 $dirty 条脏"
done

# ---------------------------------------------------------------------------
step "5 只读 rootfs 下确实只有卷与 /tmp 可写"
probe=$(docker exec katana-work-folder-mcp-staging sh -c '
  touch /probe 2>/dev/null && echo "ROOTFS_WRITABLE" || echo "rootfs-ro"
  touch /tmp/probe 2>/dev/null && echo "tmp-ok" || echo "TMP_RO"
  touch /data/work-records/.probe 2>/dev/null && { echo "vol-ok"; rm -f /data/work-records/.probe; } || echo "VOL_RO"' 2>&1)
echo "$probe" | grep -q "rootfs-ro" && ok "rootfs 只读" || bad "rootfs 可写（加固失效）"
echo "$probe" | grep -q "tmp-ok"   && ok "/tmp 可写"   || bad "/tmp 不可写"
echo "$probe" | grep -q "vol-ok"   && ok "卷可写"      || bad "卷不可写"

# ---------------------------------------------------------------------------
step "6 备份能从卷里做出 mirror"
mkdir -p "$STAGING_MIRROR"
if KATANA_MIRROR_ROOT="$STAGING_MIRROR" \
   KATANA_HELPER_IMAGE="katana-mcp:$IMAGE_TAG" \
   bash -c "sed 's/katana-\$name/katana-staging-\$name/' '$HERE/backup-volumes.sh' > /tmp/bk-staging.sh && bash /tmp/bk-staging.sh" 2>&1 | tail -3; then
  for m in work-records wiki memory; do
    if [ -d "$STAGING_MIRROR/$m.git" ]; then
      n=$(git --git-dir="$STAGING_MIRROR/$m.git" rev-list --count HEAD 2>/dev/null)
      ok "备份 $m.git（$n commits）"
    else
      bad "备份 $m.git 缺失"
    fi
  done
fi

# ---------------------------------------------------------------------------
step "结果"
printf '  PASS=%s  FAIL=%s\n' "$pass" "$fail"
[ "$KEEP" -eq 1 ] && echo "  --keep：staging 栈保留（端口 15601/15602/15605），拆用 $0 --down" || teardown
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
