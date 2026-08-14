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
# 生产目录全程只以 `:ro` 挂载，不写不改。连状态探测都在只读容器里做，且带
# `--no-optional-locks`——`git status` 默认会顺手刷新并回写 `.git/index`，那是**写**。
#
# 与生产的唯一差异是卷名、宿主端口、容器名（见 docker-compose.staging.yml）——
# 镜像、env、加固项、command 全部逐字相同，否则演练就不是演练。
#
# ---------------------------------------------------------------------------
# 为什么有「步骤 0b 脏源判定」这一段（这是本脚本最贵的一条教训）
# ---------------------------------------------------------------------------
# `cp -a` 会把**未跟踪文件一起搬进卷**。于是种卷那一刻生产恰好脏，staging 卷里
# 就带着一份脏工作区，work-folder MCP 启动即 `katana_kernel.gitops.DirtyWorkTreeError`
# （server.py configure() → kernel.reconcile），容器进 restart loop，连锁把十几条
# 下游判据全打成 FAIL。
#
# 实测：同一份代码，生产净时 25 PASS / 2 FAIL，生产脏时 14 PASS / 13 FAIL。
# **唯一变量就是种卷那一刻生产脏不脏**——读数不可信，等于没读数。
#
# 现在的做法，两件事分开：
#   1. 种卷**前**显式判定三域源仓状态，作为一条独立前置结论输出（哪个域、多少条、
#      脏在哪个前缀）。脏不脏是生产的事实，如实报出来，不算 FAIL——按硬线 7，
#      /data/work-records 的产物是三条 ronin 线的活，不归演练管。
#   2. 种进 staging 副本后，**在副本里**按真迁移的「脏仓拒迁」纪律洗净
#      （`git reset --hard` + `git clean -fd`，不带 -x 以保住 gitignored 的
#      `.katana/runtime/`），再显式核验副本干净才起栈。洗的是一次性副本，
#      生产一个字节都不动。
# 于是生产脏/净两种状态下，演练测的都是同一件事：容器栈本身。
#
# 覆盖面：
#   1. 三个容器起得来且健康
#   2. 三域的 MCP 工具面真往返（work-folder 走完整写事务，memory 写，wiki 读）
#   3. 写入真落进卷内 git，且 author 是 katana-mcp
#   4. 只读 rootfs 下 git 事务仍可提交（这是最容易翻车的一项）
#   5. 备份能从卷里做出 mirror
#
# 用法：
#   deploy/rehearse.sh                    # 跑完自动拆
#   deploy/rehearse.sh --keep             # 保留 staging 栈供手工继续戳
#   deploy/rehearse.sh --down             # 只拆
#   deploy/rehearse.sh --keep-source-dirt # 不洗副本，照搬生产脏状态（专门演练脏仓场景）
#   deploy/rehearse.sh --no-build         # 镜像必须事先备好，缺了就 FAIL（不就地构建）
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
declare -A CONTAINERS=([wiki]=katana-wiki-mcp-staging [work-folder]=katana-work-folder-mcp-staging [memory]=katana-memory-mcp-staging)

# 步骤 0b 记录的生产基线，拆除时逐字比对，作为「生产未触碰」的证据。
declare -A SRC_HEAD_BEFORE=()
declare -A SRC_DIRTY_BEFORE=()
declare -A READY=()          # 域 → 1/0，起栈后是否可用；下游判据据此 SKIP 而不是连锁 FAIL
declare -A BLOCKED_BY=()     # 域 → 不可用的根因，SKIP 时原样打出来
declare -A POST_SCRUB=()     # 域 → 洗净后、起栈前的脏条数；结果行的解耦证据
PROD_BIND_CLEAN=unknown   # 步骤 2 实测填 1/0；teardown 的「生产未触碰」[B] 证据

KEEP=0; DOWN_ONLY=0; KEEP_DIRT=0; NO_BUILD=0
SEED_ATTEMPTS="${KATANA_SEED_ATTEMPTS:-4}"   # 撞上并发写就重抄，见步骤 1
for a in "$@"; do
  case "$a" in
    --keep) KEEP=1 ;;
    --down) DOWN_ONLY=1 ;;
    --keep-source-dirt) KEEP_DIRT=1 ;;
    --no-build) NO_BUILD=1 ;;
    *) echo "用法：$0 [--keep|--down|--keep-source-dirt|--no-build]" >&2; exit 2 ;;
  esac
done

pass=0; fail=0; skip=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$*"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=$((fail+1)); }
skp()  { printf '  \033[33mSKIP\033[0m %s\n' "$*"; skip=$((skip+1)); }
note() { printf '  \033[36mNOTE\033[0m %s\n' "$*"; }
step() { printf '\n=== %s ===\n' "$*"; }

# --- 生产源仓只读探测 -------------------------------------------------------
# 一律在容器里、一律 `:ro`、一律 --no-optional-locks。三重保证「探测不写生产」。
# safe.directory：宿主目录属主是 uther(1000)，容器里以 10001 读，不加这个 git 会
# 以 dubious ownership 拒绝，表现为空输出——那会把「脏」误读成「净」，正是本脚本
# 要消灭的那类静默失真。
src_git() {
  local src="$1"; shift
  docker run --rm --user 10001:10001 -v "$src:/src:ro" --entrypoint sh \
    "katana-mcp:$IMAGE_TAG" -c \
    "git -c safe.directory='*' --no-optional-locks -C /src $*" 2>/dev/null
}
src_status() { src_git "$1" "status --porcelain=v1 --untracked-files=all"; }
src_head()   { src_git "$1" "rev-parse HEAD"; }

# staging 卷内探测（属主已 chown 到 10001，不需要 safe.directory 放宽）
vol_git() {
  local vol="$1"; shift
  docker run --rm --user 10001:10001 -v "$vol:/d" --entrypoint sh \
    "katana-mcp:$IMAGE_TAG" -c "git --no-optional-locks -C /d $*" 2>/dev/null
}

# --- 脏计数：必须区分「0 条」与「读不出来」-----------------------------------
# 上一版写的是 `vol_git ... status | grep -c .`。git 报错时 stdout 为空，grep 数出
# **0**，于是一个 `bad tree object HEAD` 的撕裂副本被读成「干净」——
# 「PASS 卷内仓干净」「洗净 0→0」「post_scrub=0/0/0」「✅ 与生产脏度无关」四条
# 全是假阴性，而且是**自己证明自己成功**的那种假。一个断言读不出被测量时必须
# 报错，不能返回一个恰好等于「健康」的值。
vol_dirty_count() {
  local out rc
  out=$(docker run --rm --user 10001:10001 -v "$1:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" \
        -c 'git --no-optional-locks -C /d status --porcelain=v1 --untracked-files=all' 2>/dev/null)
  rc=$?
  if [ "$rc" -ne 0 ]; then echo ERR; return 1; fi
  printf '%s' "$out" | grep -c .
  return 0
}

# --- 副本自洽性：HEAD 与它的 tree 都得解得开 ---------------------------------
# `cp -a` **不是原子快照**。生产 work-records 每小时 70+ 笔落账，objects/ 与 refs/
# 在不同瞬间被抄，卷里 HEAD 就会指向一个没抄进来的 tree，work-folder MCP 启动即
#   DirtyWorkTreeError: cannot verify governed repository cleanliness:
#   error: bad tree object HEAD
# 「脏工作区」和「撕裂副本」是两根独立的轴，洗净只解决前者。
vol_intact() {
  docker run --rm --user 10001:10001 -v "$1:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" -c \
    'git --no-optional-locks -C /d rev-parse --verify -q HEAD^{commit} >/dev/null \
     && git --no-optional-locks -C /d cat-file -e HEAD^{tree} \
     && git --no-optional-locks -C /d status --porcelain=v1 --untracked-files=all >/dev/null' 2>&1
}

teardown() {
  local verify="${1:-}"
  step "拆除 staging"
  KATANA_MCP_TAG="${IMAGE_TAG:-x}" "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1
  for v in "${VOLS[@]}"; do docker volume rm "$v" >/dev/null 2>&1; done
  rm -rf "$STAGING_MIRROR"
  echo "  staging 卷与 mirror 已清除"

  if [ "$verify" != "--verify" ] || [ "${#SRC_HEAD_BEFORE[@]}" -eq 0 ]; then
    echo "  （未记录生产基线，跳过比对）"
    return
  fi
  # ---------------------------------------------------------------------
  # 「生产未触碰」怎么才算证明了
  # ---------------------------------------------------------------------
  # **不能拿「HEAD 没变」当判据。** /data/work-records 是活的生产仓，三条 ronin 线
  # 在并发落账，一次演练跑十分钟，HEAD 前进是常态。第一版拿 HEAD 相等做判据，
  # 当场报出「❌ 生产 HEAD 发生变化 —— 立即查」，而演练全程只 :ro 挂载，
  # 什么都没干。假警报和漏报一样致命：它让这条回显不可信，等于没有。
  #
  # 真正能证明「没碰」的是**结构性事实**，两条，都可机检：
  #   A. 本脚本里每一处挂生产路径的 docker 调用都带 :ro（脚本自检，源码级）
  #   B. staging 栈里没有任何容器把生产路径 bind 进去（运行期实测，见步骤 2）
  # HEAD/脏条数的前后变化则降级为**信息**，并明确归因给并发工作线。
  echo "  生产未触碰核验："

  # A. 源码自检：所有挂 $src 的地方必须是 :ro
  local self="${BASH_SOURCE[0]}" ro_bad
  ro_bad=$(grep -oE -- '-v "\$src:/src[^"]*"' "$self" | sort -u | grep -v ':ro"$' || true)
  if [ -z "$ro_bad" ]; then
    echo "    [A] 源码自检：本脚本挂载生产路径的 $(grep -c -- '-v "\$src:/src:ro"' "$self") 处调用全部为 :ro"
  else
    echo "    [A] ❌ 源码自检失败，存在非只读的生产挂载：$ro_bad"
  fi

  # B. 运行期实测（步骤 2 记录）
  case "${PROD_BIND_CLEAN:-unknown}" in
    1) echo "    [B] 运行期实测：staging 四容器均未 bind 任何生产路径（零 bind mount 成立）" ;;
    0) echo "    [B] ❌ 运行期实测：有容器 bind 了生产路径 —— 立即查" ;;
    *) echo "    [B] 运行期实测：未采集（栈未起来）" ;;
  esac

  # C. 前后对照，仅作信息与归因
  echo "    [C] 生产状态前后对照（信息，非判据）："
  for d in wiki work-folder memory; do
    local src="${SRCDIRS[$d]}" h_now n_now tail_note
    h_now="$(src_head "$src")"
    n_now="$(src_status "$src" | wc -l | tr -d ' ')"
    if [ "$h_now" = "${SRC_HEAD_BEFORE[$d]}" ] && [ "$n_now" = "${SRC_DIRTY_BEFORE[$d]}" ]; then
      tail_note="无变化"
    else
      tail_note="有变化 —— 归因：并发工作线（本演练对该路径只有 :ro 句柄，写无可写）"
    fi
    printf '         %-12s HEAD %s→%s，脏 %s→%s 条；%s\n' \
           "$d" "${SRC_HEAD_BEFORE[$d]:0:8}" "${h_now:0:8}" \
           "${SRC_DIRTY_BEFORE[$d]}" "$n_now" "$tail_note"
  done

  if [ -z "$ro_bad" ] && [ "${PROD_BIND_CLEAN:-unknown}" != "0" ]; then
    echo "  ✅ 生产未触碰（A 源码自检 + B 运行期零 bind 双证）"
  else
    echo "  ❌ 生产未触碰不成立 —— 见上面标 ❌ 的那条"
  fi
}

if [ "$DOWN_ONLY" -eq 1 ]; then
  IMAGE_TAG="${IMAGE_TAG:-$(git -C "$HERE/.." rev-parse --short HEAD 2>/dev/null)}"
  teardown; exit 0
fi

# ---------------------------------------------------------------------------
step "0 前置"
if [ -z "$IMAGE_TAG" ]; then
  IMAGE_TAG="$(git -C "$HERE/.." rev-parse --short HEAD 2>/dev/null)"
fi
export KATANA_MCP_TAG="$IMAGE_TAG"
# 默认 tag 跟 HEAD，于是**每出一个新 commit，「照文档跑」这条路就断一次**
# （`FAIL 缺镜像 katana-mcp:<新 sha>` → exit 1）。一份要求先手工建两个镜像才能跑的
# ops 工装，等于默认不可跑。缺就按 deploy/README.md 的那两条命令**就地建**，
# 这样镜像与 HEAD 恒定同源，也不可能测成别的 commit。
# 要严格校验「镜像必须事先备好」，用 --no-build。
ensure_image() {
  local img="$1" df="$2" ctx="$3" log="/tmp/rehearse-build-${1}-${IMAGE_TAG}.log"
  if docker image inspect "$img:$IMAGE_TAG" >/dev/null 2>&1; then
    ok "镜像 $img:$IMAGE_TAG 在位"; return 0
  fi
  if [ "$NO_BUILD" -eq 1 ]; then
    bad "缺镜像 $img:$IMAGE_TAG 且指定了 --no-build —— 见 deploy/README.md「构建」"; return 1
  fi
  echo "  缺 $img:$IMAGE_TAG，按 deploy/README.md 就地构建（context=$ctx）…"
  if docker build -f "$HERE/../$df" --build-arg GIT_REVISION="$IMAGE_TAG" \
       -t "$img:$IMAGE_TAG" "$HERE/../$ctx" >"$log" 2>&1; then
    ok "镜像 $img:$IMAGE_TAG 就地构建完成（日志 $log）"
  else
    bad "镜像 $img:$IMAGE_TAG 构建失败（日志 $log）"
    tail -15 "$log" | sed 's/^/       /'
    return 1
  fi
}
ensure_image katana-mcp       mcp/Dockerfile                . || exit 1
ensure_image katana-embedding services/embedding/Dockerfile services/embedding || exit 1
for d in wiki work-folder memory; do
  [ -d "${SRCDIRS[$d]}/.git" ] && ok "生产源 ${SRCDIRS[$d]} 在位（只读使用）" || { bad "缺源 ${SRCDIRS[$d]}"; exit 1; }
done

# ---------------------------------------------------------------------------
step "0b 种卷前置：三域生产源仓状态判定"
# 这一段是**独立前置结论**，不是下游判据的一部分。它回答一个问题：
#   「这次演练的种子，是从什么状态的生产拷出来的？」
# 不回答这个问题，后面所有读数都无法解释。
dirty_total=0
dirty_domains=()
for d in wiki work-folder memory; do
  src="${SRCDIRS[$d]}"
  st="$(src_status "$src")"; st_rc=$?
  head="$(src_head "$src")"
  if [ "$st_rc" -ne 0 ]; then
    # 源侧同样不许把「读不出来」记成 0 条 —— 那正是本轮骗过读数的形态。
    bad "源 $d 状态读取失败（git status 非零退出）—— 按 FAIL 处理，不当成干净"
    SRC_HEAD_BEFORE[$d]="$head"; SRC_DIRTY_BEFORE[$d]=ERR
    continue
  fi
  n=$(printf '%s' "$st" | grep -c . )
  SRC_HEAD_BEFORE[$d]="$head"
  SRC_DIRTY_BEFORE[$d]="$n"
  if [ "$n" -eq 0 ]; then
    ok "源 $d 干净（HEAD ${head:0:12}，0 条）"
  else
    n_mod=$(printf '%s\n' "$st" | grep -cv '^??')
    n_untracked=$(printf '%s\n' "$st" | grep -c '^??')
    dirty_total=$((dirty_total+n))
    dirty_domains+=("$d")
    # 报到「脏在哪个前缀」这一层——只说条数没法判断是谁在写。
    top="$(printf '%s\n' "$st" | sed 's/^...//' | cut -d/ -f1 | sort | uniq -c | sort -rn | head -3 \
           | awk '{printf "%s(%s) ", $2, $1}')"
    note "源 $d 脏 $n 条（HEAD ${head:0:12}；已跟踪改动 $n_mod，未跟踪 $n_untracked）"
    note "     脏在：$top"
  fi
done

if [ "$dirty_total" -eq 0 ]; then
  SEED_MODE="verbatim"
  echo
  echo "  判定：三域生产源仓全部干净 → staging 卷逐字照搬即可，无需洗。"
elif [ "$KEEP_DIRT" -eq 1 ]; then
  SEED_MODE="verbatim"
  echo
  echo "  判定：生产脏 $dirty_total 条（${dirty_domains[*]}），但指定了 --keep-source-dirt"
  echo "        → 照搬脏状态种卷。**预期** work-folder MCP 会 DirtyWorkTreeError 起不来，"
  echo "        这是刻意演练脏仓场景，不是回归。"
else
  SEED_MODE="scrub"
  echo
  echo "  判定：生产脏 $dirty_total 条（${dirty_domains[*]}）。"
  echo "        cp -a 会把未跟踪文件一并搬进卷，脏工作区会让 work-folder MCP 启动即"
  echo "        DirtyWorkTreeError 进 restart loop，连锁污染全部下游读数。"
  echo "        → 按真迁移的「脏仓拒迁」纪律，**在 staging 副本内**洗净后再起栈"
  echo "        （git reset --hard + git clean -fd，不带 -x 以保住 gitignored 的 .katana/runtime）。"
  echo "        生产一个字节都不动；要照搬脏状态请用 --keep-source-dirt。"
  # 硬线 7：/data/work-records 里的产物是三条 ronin 线的活，本卷只负责别让它污染读数。
  echo "        注：生产那些脏文件本身不归演练处理，此处不做任何清理动作。"
fi

# ---------------------------------------------------------------------------
step "1 从生产目录 cp -a 种 staging 卷（只读挂载，与真迁移同款）"
teardown >/dev/null 2>&1
for d in wiki work-folder memory; do
  vol="${VOLS[$d]}"; src="${SRCDIRS[$d]}"
  # 与 migrate-data-to-volumes.sh 同款：root 身份 cp -a 全量（含 gitignored 的
  # .katana/runtime），再 chown 给镜像的运行用户。
  # 但 cp -a 抄的是一个**正在被写**的仓，可能抄出撕裂副本，故：抄完立刻验自洽，
  # 不自洽就整卷重抄。重抄窗口是秒级，落账间隔是分钟级，几次之内必成；连续失败
  # 则显式 FAIL，绝不带着一个解不开 HEAD 的卷往下走。
  seeded=0
  for attempt in $(seq 1 "$SEED_ATTEMPTS"); do
    docker volume rm "$vol" >/dev/null 2>&1
    docker volume create "$vol" >/dev/null
    pin="$(src_head "$src")"          # 抄之前先记锚点，用来证明窗口内源仓是否前进
    if ! docker run --rm --user 0:0 -v "$vol:/dst" -v "$src:/src:ro" --entrypoint sh \
         "katana-mcp:$IMAGE_TAG" -c "cp -a /src/. /dst/ && chown -R 10001:10001 /dst" >/dev/null 2>&1; then
      note "$d 第 $attempt 次 cp -a 失败，重抄"
      continue
    fi
    # 故障注入（**仅自测用**）：把前 N 次抄出来的副本人为撕裂，用来证明重抄这条
    # 路真的会跑而不是「恰好没撞上」。真实撕裂是概率事件（落账约 1 笔/分钟，
    # 抄一次十几秒），连跑两次都没撞上不等于免疫。默认 0，不注入。
    if [ "${KATANA_SEED_FORCE_TEAR:-0}" -gt 0 ] && [ "$attempt" -le "${KATANA_SEED_FORCE_TEAR:-0}" ]; then
      docker run --rm --user 0:0 -v "$vol:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" -c \
        't=$(git -C /d rev-parse HEAD^{tree}); rm -f /d/.git/objects/${t%${t#??}}/${t#??}' >/dev/null 2>&1
      note "$d 第 $attempt 次：故障注入人为撕裂副本（KATANA_SEED_FORCE_TEAR）"
    fi
    if intact_err=$(vol_intact "$vol"); then
      post="$(src_head "$src")"
      n=$(vol_git "$vol" "rev-list --count HEAD")
      if [ "$attempt" -gt 1 ]; then
        ok "$d 卷已种入且自洽（$n commits；第 $attempt 次抄成，前 $((attempt-1)) 次撞上并发写）"
      else
        ok "$d 卷已种入且自洽（$n commits，HEAD 与其 tree 均解得开）"
      fi
      if [ "$pin" != "$post" ]; then
        note "$d 种卷窗口内源仓前进 ${pin:0:8}→${post:0:8}（生产确在并发落账，副本仍自洽）"
      fi
      seeded=1; break
    fi
    note "$d 第 $attempt 次抄到撕裂副本，重抄：$(printf '%s' "$intact_err" | tail -1)"
  done
  if [ "$seeded" -ne 1 ]; then
    bad "$d 连续 $SEED_ATTEMPTS 次都抄到撕裂副本 —— 种卷一致性没解决，本域读数不可信"
    POST_SCRUB[$d]=ERR
    continue
  fi

  if [ "$SEED_MODE" = "scrub" ] && [ "${SRC_DIRTY_BEFORE[$d]}" != "0" ]; then
    before=$(vol_dirty_count "$vol")
    docker run --rm --user 10001:10001 -v "$vol:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" -c \
      "git -C /d reset --hard >/dev/null 2>&1 && git -C /d clean -fd >/dev/null 2>&1" >/dev/null 2>&1
    after=$(vol_dirty_count "$vol")
    note "$d 副本内洗净：$before → $after 条（生产未触碰）"
  fi

  # 起栈**前**就把 work-folder 的启动前置条件核验掉。不核验的代价实测过：
  # 一条 DirtyWorkTreeError 在下游炸成 11 条 FAIL，而真正的根因一个字都没打出来。
  clean_n=$(vol_dirty_count "$vol")
  POST_SCRUB[$d]="$clean_n"
  if [ "$clean_n" = "ERR" ]; then
    bad "$d staging 卷内仓脏计数**读不出来**（git status 非零退出）—— 按 FAIL 处理，绝不当成 0 条"
    vol_intact "$vol" | tail -2 | sed 's/^/       /'
  elif [ "$clean_n" -eq 0 ]; then
    ok "$d staging 卷内仓干净（MCP 启动前置条件满足）"
  elif [ "$KEEP_DIRT" -eq 1 ]; then
    note "$d staging 卷内仓脏 $clean_n 条 —— --keep-source-dirt 下这是预期的"
  else
    bad "$d staging 卷内仓仍脏 $clean_n 条，洗净失败 —— 下游读数不可信，先查这条"
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
  [ "${healthy:-0}" -ge 4 ] && break
  sleep 2
done
if [ "${healthy:-0}" -ge 4 ]; then ok "四个容器 healthy（含 embedding）"; else bad "健康检查未全绿（healthy=$healthy）"; "${COMPOSE[@]}" ps; fi

# 零 bind mount 是本工程的核心性质，也是「生产未触碰」的运行期证据（见 teardown 的 [B]）。
# 实测而不是靠读 compose 文件：compose 的 merge 语义踩过坑（ports 追加合并那次），
# 声明与实际起出来的东西不一定一致，要问就问 dockerd。
prod_binds=$(for c in $("${COMPOSE[@]}" ps -q 2>/dev/null); do
               docker inspect -f '{{$n := .Name}}{{range .Mounts}}{{if eq .Type "bind"}}{{$n}} {{.Source}}{{"\n"}}{{end}}{{end}}' "$c" 2>/dev/null
             done | grep -E ' /data/(wiki|work-records|memory)(/|$)' || true)
if [ -z "$prod_binds" ]; then
  PROD_BIND_CLEAN=1
  ok "staging 四容器零 bind mount 生产路径（宿主 dockerd 实测）"
else
  PROD_BIND_CLEAN=0
  bad "有容器把生产路径 bind 进去了 —— 这是本工程最不该破的性质"
  echo "$prod_binds" | sed 's/^/       /'
fi

for d in wiki work-folder memory; do
  path="/mcp"; [ "$d" = "memory" ] && path="/t/uther/mcp"   # memory 多租户挂载
  if curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:${PORTS[$d]}$path" | grep -qE '^[24]'; then
    ok "$d 端口 ${PORTS[$d]} 有响应"
    READY[$d]=1
  else
    READY[$d]=0
    c="${CONTAINERS[$d]}"
    restarts=$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo "?")
    status=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "?")
    # 一次把根因打全：restart 次数 + 容器日志最后几行。下游一律 SKIP 不再复述。
    root=$(docker logs --tail 40 "$c" 2>&1 | grep -oE '[A-Za-z_.]*(Error|Exception)[A-Za-z]*' | tail -1)
    BLOCKED_BY[$d]="容器 $c status=$status restarts=$restarts${root:+ 根因=$root}"
    bad "$d 端口 ${PORTS[$d]} 无响应 —— ${BLOCKED_BY[$d]}"
    echo "       ↓ $c 最后 12 行日志（根因就在这里，别去下游找）"
    docker logs --tail 12 "$c" 2>&1 | sed 's/^/       /'
  fi
done

# 下游判据的统一闸门：前置不满足就 SKIP，不制造连锁 FAIL。
# 一条根因应当只产生一条 FAIL；把它复述十几遍不增加任何信息，只会让读数不可解释。
gate() {  # gate <域> <判据描述> —— 返回 0 表示可以继续跑
  local d="$1" what="$2"
  if [ "${READY[$d]:-0}" -eq 1 ]; then return 0; fi
  skp "$what（前置未满足：${BLOCKED_BY[$d]:-$d 未就绪}）"
  return 1
}

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

gate work-folder "work-folder wf_create"        && { chk wf_create   && ok "work-folder wf_create 成功"    || bad "work-folder wf_create 失败"; }
gate work-folder "work-folder fs_create"        && { chk fs_create   && ok "work-folder fs_create 提交成功" || bad "work-folder fs_create 失败"; }
gate memory      "memory memory_create"         && { chk mem_create  && ok "memory memory_create 成功"      || bad "memory memory_create 失败"; }
gate wiki        "wiki wiki_search"             && { chk wiki_search && ok "wiki wiki_search 成功"          || bad "wiki wiki_search 失败"; }
gate work-folder "work-folder wf_search"        && { chk wf_search   && ok "work-folder wf_search 成功"     || bad "work-folder wf_search 失败"; }

# ---------------------------------------------------------------------------
step "3b embedding 服务与向量臂"
if gate work-folder "embedding 可达性与维度（经 work-folder 容器内探测）"; then
  emb=$(docker exec "${CONTAINERS[work-folder]}" python -c "
import os, httpx, json
url = os.environ['KATANA_EMBEDDING_ENDPOINT']
r = httpx.post(url, json={'input': ['向量臂自检']}, timeout=30)
print(json.dumps({'status': r.status_code, 'dim': len(r.json()['data'][0]['embedding'])}))
" 2>&1 | tail -1)
  echo "  $emb"
  echo "$emb" | grep -q '"status": 200' && ok "MCP 容器内可达 embedding 服务" || bad "MCP 容器内够不到 embedding"
  echo "$emb" | grep -q '"dim": 512'    && ok "向量维度 512（与索引 schema 一致）" || bad "向量维度不符"
fi

# ---------------------------------------------------------------------------
step "4 写入真落进卷内 git，且 author 正确"
for d in work-folder memory; do
  gate "$d" "$d 卷内 commit author 与事务后洁净度" || continue
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
if gate work-folder "只读 rootfs 加固三项"; then
  probe=$(docker exec "${CONTAINERS[work-folder]}" sh -c '
    touch /probe 2>/dev/null && echo "ROOTFS_WRITABLE" || echo "rootfs-ro"
    touch /tmp/probe 2>/dev/null && echo "tmp-ok" || echo "TMP_RO"
    touch /data/work-records/.probe 2>/dev/null && { echo "vol-ok"; rm -f /data/work-records/.probe; } || echo "VOL_RO"' 2>&1)
  echo "$probe" | grep -q "rootfs-ro" && ok "rootfs 只读" || bad "rootfs 可写（加固失效）"
  echo "$probe" | grep -q "tmp-ok"   && ok "/tmp 可写"   || bad "/tmp 不可写"
  echo "$probe" | grep -q "vol-ok"   && ok "卷可写"      || bad "卷不可写"
fi

# ---------------------------------------------------------------------------
step "6 备份能从卷里做出 mirror"
# 备份走的是卷，不经 MCP 进程——MCP 起不来时这一条仍然该跑，也仍然该有结论。
#
# **绝不能把这段写成 `if <pipeline> | tail -3; then <判据>; fi`。** 上一版就是那么写的，
# 有两处静默失真，实测同时发作：
#   1. `| tail -3` 把 backup-volumes.sh 逐域的 FAIL/WARN（走 stderr）截没了，
#      只剩最后三行，第一个域的报错一个字都看不见；
#   2. 那个脚本任一域出问题就返回 1，叠上 `set -o pipefail`，`if` 判假 →
#      **三条备份判据整段不执行**。于是这一步既不 PASS 也不 FAIL，凭空消失，
#      而总计里看不出少了三条。
# 现在：输出全留、退出码单独成一条判据、三条校验无论如何都跑。
mkdir -p "$STAGING_MIRROR"
bk_out=$(KATANA_MIRROR_ROOT="$STAGING_MIRROR" \
         KATANA_HELPER_IMAGE="katana-mcp:$IMAGE_TAG" \
         bash -c "sed 's/katana-\$name/katana-staging-\$name/' '$HERE/backup-volumes.sh' > /tmp/bk-staging.sh && bash /tmp/bk-staging.sh" 2>&1)
bk_rc=$?
echo "$bk_out" | sed 's/^/       /'
if [ "$bk_rc" -eq 0 ]; then
  ok "backup-volumes.sh 退出码 0"
else
  bad "backup-volumes.sh 退出码 $bk_rc —— 逐域结论见上面几行"
fi
declare -A VOLOF=([work-records]=katana-staging-work-records [wiki]=katana-staging-wiki [memory]=katana-staging-memory)
for m in work-records wiki memory; do
  if [ ! -d "$STAGING_MIRROR/$m.git" ]; then
    bad "备份 $m.git 缺失"
    continue
  fi
  n=$(git --git-dir="$STAGING_MIRROR/$m.git" rev-list --count HEAD 2>/dev/null)
  # 「目录在」不等于「备份全」。逐字比对源卷与 mirror 的 commit 数——
  # work-records 那次就是 clone 退出非零、mirror 根本没建出来，而判据只看目录。
  sn=$(vol_git "${VOLOF[$m]}" "rev-list --count HEAD")
  if [ -n "$sn" ] && [ "$n" = "$sn" ]; then
    ok "备份 $m.git 完整（commit $n = 源卷 $sn）"
  else
    bad "备份 $m.git commit 数不符（mirror=$n 源卷=$sn）"
  fi
  # 产物必须归调用者：root-owned 的备份等于「只有 Docker 动得了」，那不叫备份。
  owner=$(stat -c '%u:%g' "$STAGING_MIRROR/$m.git/HEAD" 2>/dev/null)
  if [ "$owner" = "$(id -u):$(id -g)" ]; then
    ok "备份 $m.git 属主为调用者（$owner，可读可删，无 root 残留）"
  else
    bad "备份 $m.git 属主是 $owner，期望 $(id -u):$(id -g) —— 会留下只有 root 能清的残留"
  fi
done

# ---------------------------------------------------------------------------
step "6b 负例自证：源侧异常的三种形态（含本轮骗过读数的那条）"
# 光看「三域都出了 mirror」证明不了健壮性——上一版三域里有一域根本没出，读数照样
# 「看起来没问题」。这一步在**一次性造出来的小仓**上主动制造两种源侧 ref 异常，
# 分别断言两件相反的事：
#   1. 不可读的 ref（0600/000，属主与 helper 不同）——修好之后必须**被吃掉**，
#      因为读源已改走 root。这正是 work-records 那条 0600 tag 的形态。
#   2. 真正损坏的 ref（写进 null OID）——必须**显式 FAIL 且带 git 原文**，
#      绝不允许再退回「静默跳过」。
# 全程只碰自己造的 katana-staging-negctl 卷，跑完即删，不碰三域任何数据。
NEG_VOL=katana-staging-negctl
NEG_MIRROR=/tmp/katana-negctl-mirror

neg_setup() {
  docker volume rm "$NEG_VOL" >/dev/null 2>&1
  rm -rf "$NEG_MIRROR"; mkdir -p "$NEG_MIRROR"
  docker volume create "$NEG_VOL" >/dev/null
  docker run --rm --user 0:0 -v "$NEG_VOL:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" -c '
    set -e
    cd /d && git init -q
    echo probe > a.md && git add a.md
    git -c user.email=n@l -c user.name=n commit -qm seed
    git -c user.email=n@l -c user.name=n tag -a probe -m probe
    chown -R 10001:10001 /d' >/dev/null 2>&1
  case "$1" in
    unreadable) docker run --rm --user 0:0 -v "$NEG_VOL:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" \
                  -c 'chmod 000 /d/.git/refs/tags/probe' >/dev/null 2>&1 ;;
    broken)     docker run --rm --user 0:0 -v "$NEG_VOL:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" \
                  -c 'printf "%040d\n" 0 > /d/.git/refs/tags/probe' >/dev/null 2>&1 ;;
    torn)       # 复刻撕裂副本：HEAD 的 commit 在，它的 tree 不在 —— 与生产并发写
                # 被 cp -a 抄成两半的结果逐字同形（bad tree object HEAD）。
                docker run --rm --user 0:0 -v "$NEG_VOL:/d" --entrypoint sh "katana-mcp:$IMAGE_TAG" \
                  -c 't=$(git -C /d rev-parse HEAD^{tree}); rm -f /d/.git/objects/${t%${t#??}}/${t#??}' >/dev/null 2>&1 ;;
  esac
}
neg_run() {
  KATANA_MIRROR_ROOT="$NEG_MIRROR" KATANA_HELPER_IMAGE="katana-mcp:$IMAGE_TAG" \
  KATANA_BACKUP_DOMAINS=negctl \
  bash -c "sed 's/katana-\$name/katana-staging-\$name/' '$HERE/backup-volumes.sh' > /tmp/bk-neg.sh && bash /tmp/bk-neg.sh" 2>&1
}

neg_setup unreadable
neg_out=$(neg_run); neg_rc=$?
if [ "$neg_rc" -eq 0 ] && echo "$neg_out" | grep -q '^FULL negctl'; then
  ok "不可读 ref（模式 000，属主非 helper）不再阻断备份 —— 这是 work-records 那条 0600 tag 的形态"
else
  bad "不可读 ref 仍然阻断备份（rc=$neg_rc）—— 对源侧权限依旧敏感"
  echo "$neg_out" | tail -4 | sed 's/^/       /'
fi

neg_setup broken
neg_out=$(neg_run); neg_rc=$?
if [ "$neg_rc" -ne 0 ] && echo "$neg_out" | grep -q '     git: '; then
  ok "真损坏 ref（null OID）显式 FAIL 且带 git 原文（rc=$neg_rc）—— 不会再静默"
  echo "$neg_out" | grep -E '^FAIL|     git: ' | head -2 | sed 's/^/       /'
else
  bad "真损坏 ref 没能显式炸出来（rc=$neg_rc）—— 静默失真回归了"
  echo "$neg_out" | tail -4 | sed 's/^/       /'
fi
# 第三种形态 —— **这一条正是本轮骗过读数的那个**。
# 撕裂副本上 `git status` 直接报 `error: bad tree object HEAD` 并非零退出，stdout 为空。
# 旧写法 `... | grep -c .` 把它数成 0，于是「卷内仓干净」PASS、「洗净 0→0」、
# post_scrub=0/0/0、✅「与生产脏度无关」全部成立——四条断言一起说谎，
# 而真实情况是 work-folder 容器起不来。现在必须显式 ERR。
neg_setup torn
neg_cnt=$(vol_dirty_count "$NEG_VOL")
if [ "$neg_cnt" = "ERR" ]; then
  ok "撕裂副本的脏计数显式报 ERR（不再被数成 0 条）—— 本轮假绿的那条形态已封"
  vol_intact "$NEG_VOL" | tail -1 | sed 's/^/       /'
else
  bad "撕裂副本被读成「$neg_cnt 条」—— 假阴性回归，读数不可信"
fi
# 同一形态在种卷路径上也必须被拦：vol_intact 要判它不自洽
if vol_intact "$NEG_VOL" >/dev/null 2>&1; then
  bad "vol_intact 认为撕裂副本是自洽的 —— 种卷重抄逻辑会被绕过"
else
  ok "vol_intact 判定撕裂副本不自洽 —— 种卷阶段就会重抄而不是带病起栈"
fi

docker volume rm "$NEG_VOL" >/dev/null 2>&1; rm -rf "$NEG_MIRROR" /tmp/bk-neg.sh
echo "  负例用的一次性卷与 mirror 已清除"

# ---------------------------------------------------------------------------
step "结果"
printf '  PASS=%s  FAIL=%s  SKIP=%s\n' "$pass" "$fail" "$skip"
# ---------------------------------------------------------------------------
# 读数与生产脏净解耦的**一行证据**
# ---------------------------------------------------------------------------
# 两次不同脏度的运行要能直接对比，就得把「输入脏度」和「起栈时的实际输入」分开印：
#   · seed_dirty  是这一刻生产的脏度 —— 每次都不同，是环境噪声
#   · post_scrub  是洗完之后、真正喂给容器栈的脏度 —— **必须恒为 0/0/0**
# 只要 post_scrub 这段两次运行相同，PASS/FAIL 计数就可比；它一旦不是 0/0/0，
# 这一轮读数就不可信，不必再往下解释任何一条 FAIL。
printf '  读数解耦证据：\n'
printf '    seed_mode=%s  seed_dirty=%s（%s）\n' \
       "$SEED_MODE" "$dirty_total" "${dirty_domains[*]:-无}"
scrub_line=""; post_line=""; post_bad=0
for d in wiki work-folder memory; do
  scrub_line+="$d:${SRC_DIRTY_BEFORE[$d]:-?}→${POST_SCRUB[$d]:-?} "
  post_line+="${POST_SCRUB[$d]:-?}/"
  [ "${POST_SCRUB[$d]:-1}" != "0" ] && post_bad=1
done
printf '    scrub=%s\n' "$scrub_line"
printf '    post_scrub=%s  ← 这一段两次运行相同则读数可比\n' "${post_line%/}"
if [ "$post_bad" -eq 0 ]; then
  printf '    ✅ 喂给容器栈的输入恒为干净仓，PASS/FAIL 计数与生产脏度无关\n'
else
  printf '    ❌ 仍有域带脏起栈，本轮 PASS/FAIL 计数不可与其它轮次相比\n'
fi
if [ "$skip" -gt 0 ]; then
  echo "  SKIP 不是「没测」的借口，是「前置塌了，测了也没意义」——根因见上面唯一那条 FAIL。"
fi
# ---------------------------------------------------------------------------
# 判据 11「rehearse.sh 全绿」的口径 —— 写死在这里，免得靠嘴解释
# ---------------------------------------------------------------------------
# 全绿 == FAIL=0 **且** SKIP=0。
# SKIP 的语义是「前置塌了，这条判据根本没跑」，它把连锁塌方从十几条 FAIL 收敛成
# 一条 FAIL + 若干 SKIP，那是为了**可读**，不是为了好看。没跑过的判据不能算通过，
# 否则「把判据 SKIP 掉」就成了让读数变绿的最省事办法——那正是要防的作弊面。
# 故退出码对 FAIL 与 SKIP 一视同仁。
printf '  判据 11 口径：全绿 = FAIL=0 且 SKIP=0（SKIP=没跑过，不充数）→ 本轮%s\n' \
       "$([ "$fail" -eq 0 ] && [ "$skip" -eq 0 ] && echo '全绿' || echo "未全绿（FAIL=$fail SKIP=$skip）")"
if [ "$KEEP" -eq 1 ]; then
  echo "  --keep：staging 栈保留（端口 15601/15602/15605），拆用 $0 --down"
else
  teardown --verify
fi
exit $([ "$fail" -eq 0 ] && [ "$skip" -eq 0 ] && echo 0 || echo 1)
