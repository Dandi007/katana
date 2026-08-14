#!/usr/bin/env bash
# dd-stall-probe.sh —— 判定一条 dev-dispatch implement attempt 是否卡死。
#
# **这不是容器部署工装**，放在 deploy/ 是因为它和 rehearse.sh 同属本卷的确定性 ops
# 工具面（只读判定、可复核、无副作用）。它不改任何产品代码，也不碰 dd 的状态机——
# 只回答一个问题并给出退出码，由调用者决定要不要 cancel。
#
# ---------------------------------------------------------------------------
# 为什么需要它
# ---------------------------------------------------------------------------
# dev_katana_search_wiring_01 与 _02 先后卡死，两次都是靠人盯着表、凭感觉判「它是不是
# 还在跑」。中间我用错过两个信号：
#   · 「进程还在」——01 卡死 72 分钟期间进程一直在，etime 一直涨；
#   · 「socket 句柄数为 0」——实测三个**健康**的 opencode（1:56 / 55:52 / 38:48）
#     同样是 0，这个信号根本不成立，用它判过一次是错的。
# 真正能区分的只有**产出**：健康作业会往 run target / workspace 写东西。
#
# ---------------------------------------------------------------------------
# 判据（阈值按 01/02 的实测签名标定）
# ---------------------------------------------------------------------------
# **活性面 = run target ∪ workspace-repo，取两者最新写入。** 两个面缺一不可，
# 只量其中之一必然出错（下面「被测面为什么必须含 workspace」有实测代价）。
#
# 先看终局信号，再看停滞信号，顺序不能反（已收束的 run 同样「长时间零写入」）：
#   1. nodes/ 非空，或 events.jsonl 出现 "ev":"stop"          → NORMAL（已产出/已收束）
#   2. 否则：max(run target 最新写入, workspace-repo 最新写入) 距今 ≥ STALL_MINUTES
#      且 nodes/ 为空                                          → STALLED
#   3. 否则                                                     → RUNNING
# workspace-repo 由 job.json 的 config_path 反查，也可用 verdict_for 的第二参显式给。
# 判定输出把两面的静默时长**分开打印**，避免口径错位再次藏在一个合并数字后面。
#
# ---------------------------------------------------------------------------
# 被测面为什么必须含 workspace（这条是花了代价换来的）
# ---------------------------------------------------------------------------
# 第一版只量 run target，判据文字写的是「run target 零写入 ≥ 阈值 → STALLED」。
# 但**节点收束前 run target 本来就不写**：01/02 之所以「run target 静默」与「真的死了」
# 两个信号重合，只是因为它们整体死了——那是巧合，不是因果。
# 实测 dev_katana_search_wiring_05：run target 静默 21 分钟的同一时刻，workspace
# 1 分钟前刚落码（已产出 backfill.py / search_hook.py，git status 4 改 2 新增）。
# 按第一版会在第 30 分钟把这条**正在成功**的单判成 STALLED —— 不是漏报，是误杀。
# 而当时的「标定依据」引用的恰恰是 workspace 的写入间隔，被测面却没有 workspace，
# 属标定文字与实现口径的错位。**改判据时这一段与实现必须同步改，否则照文件头复核
# 的人会重新推出错误判据。**
#
# STALL_MINUTES 默认 30，标定依据（全部为本机实测，非估计）：
#   · 健康 implementer 节点完成耗时 = 988s ≈ 16.5 分钟
#     （job-2026-08-14T175654-eec8a6e8 的 elapsed_ms=988405）
#   · 健康在跑作业的 workspace 写入间隔 = 2s / 94s / 1378s
#   · 01 卡死签名：两面零写入 64/137 分钟；02 卡死签名：两面零写入 31/63 分钟
#   取 30 分钟 ≈ 健康完成耗时的两倍，既不会误杀慢作业，也不必等满引擎的
#   node_timeout（10800s = 3 小时）。
#
# 用法：
#   deploy/dd-stall-probe.sh <run-target-dir> [--minutes N]
#   deploy/dd-stall-probe.sh --self-test        # 三例自证（含 workspace 活跃那例）
#
# 退出码：0=NORMAL/RUNNING（不要处置） 1=STALLED（可处置） 2=用法或输入错误
set -uo pipefail

STALL_MINUTES="${KATANA_DD_STALL_MINUTES:-30}"

# --- 从 run target 反查 workspace-repo ---------------------------------------
# job.json 的 config_path 形如 .../<attempt>/implement/stage-resources/implement，
# workspace-repo 是它的兄弟：.../<attempt>/implement/workspace-repo
workspace_for() {
  # 用一次 grep 定位，不要逐个 job.json 起 python：/data/loop-engine/jobs 下有
  # 4000+ 个目录，逐个起解释器实测要跑几分钟，探针慢到没法放进轮询循环。
  local R="$1" j cfg
  j=$(grep -l -F "\"target_dir\": \"$R\"" /data/loop-engine/jobs/*/job.json 2>/dev/null | head -1)
  [ -z "$j" ] && return 1
  cfg=$(python3 -c "import json;print(json.load(open('$j')).get('config_path',''))" 2>/dev/null)
  [ -z "$cfg" ] && return 1
  echo "$(dirname "$(dirname "$cfg")")/workspace-repo"
}

newest_mtime() { find "$1" -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1 | cut -d. -f1; }

verdict_for() {
  # $1 = run target dir，$2 = workspace-repo（可选，缺省自动反查）
  # 回显一行 "<VERDICT> <说明>"，并 return 0/1。
  local R="$1" WS="${2:-}"
  [ -d "$R" ] || { echo "INPUT_ERROR 目录不存在: $R"; return 2; }
  [ -f "$R/events.jsonl" ] || { echo "INPUT_ERROR 缺 events.jsonl: $R"; return 2; }

  local nodes stop now rt_m ws_m newest quiet_m rt_q ws_q
  nodes=$(ls -A "$R/nodes" 2>/dev/null | wc -l)
  # grep -c 无命中时**既打印 0 又返回 1**，写成 `|| echo 0` 会得到 "0\n0"，
  # 后面的 [ "$stop" -gt 0 ] 直接 integer expected 报错。自证第一次跑就撞到了。
  stop=$(grep -c '"ev":"stop"' "$R/events.jsonl" 2>/dev/null); stop="${stop:-0}"

  # 1) 终局信号优先：已收束的 run 也会长时间零写入，先判它否则必然误报
  if [ "$nodes" -gt 0 ] || [ "$stop" -gt 0 ]; then
    echo "NORMAL nodes=$nodes stop_events=$stop（已产出或已收束）"
    return 0
  fi

  # 2) 活性面 = run target ∪ workspace-repo，取两者中**最新**的一次写入。
  #
  # 这里曾经只量 run target，是一次标定与实现的口径错位：标定文字用的是
  # 「健康作业 workspace 写入间隔 2s/94s/1378s」，被测面却没有 workspace。
  # 后果不是漏报而是**误杀**——run target 在节点收束前本来就不写，01/02 之所以
  # 两个信号重合，只是因为它们整体死了。实测 dev_katana_search_wiring_05：
  #   run target 零写入 18 分钟，同一时刻 workspace 8 秒前刚写
  #   （已落 backfill.py / search_hook.py，git status 4 改 2 新增）
  # 按旧实现会在第 30 分钟把这条正在成功的单判成 STALLED 并处置掉。
  [ -z "$WS" ] && WS="$(workspace_for "$R" || true)"
  now=$(date +%s)
  rt_m=$(newest_mtime "$R")
  [ -z "$rt_m" ] && { echo "INPUT_ERROR 目录内无文件: $R"; return 2; }
  newest="$rt_m"
  ws_q="n/a"
  if [ -n "$WS" ] && [ -d "$WS" ]; then
    ws_m=$(newest_mtime "$WS")
    if [ -n "$ws_m" ]; then
      ws_q="$(( (now - ws_m) / 60 ))分"
      [ "$ws_m" -gt "$newest" ] && newest="$ws_m"
    fi
  fi
  rt_q="$(( (now - rt_m) / 60 ))分"
  quiet_m=$(( (now - newest) / 60 ))

  local detail="run_target静默=${rt_q} workspace静默=${ws_q}"
  if [ "$quiet_m" -ge "$STALL_MINUTES" ]; then
    echo "STALLED 两面均零写入 ${quiet_m} 分钟（阈值 ${STALL_MINUTES}）nodes=0 stop=0 [$detail]"
    return 1
  fi
  echo "RUNNING 最近写入 ${quiet_m} 分钟前（未达阈值 ${STALL_MINUTES}）nodes=0 stop=0 [$detail]"
  return 0
}

self_test() {
  # 正例用真实且**已终局**的 run（终局态不会再变，可长期当夹具）。
  # 反例**不能**直接用 02 那个真 run：cancel 会往 run target 写东西，它的 mtime 因此
  # 被刷新，自证第一次跑就把已知卡死的 02 判成了 RUNNING。夹具依赖会漂移的活状态
  # 就不是夹具。故反例改为按 01/02 的实测签名**合成**：events.jsonl 止于 dispatch、
  # nodes/ 空、全目录 mtime 回拨到阈值之前。
  local healthy_run=/data/loop-engine/runs/2026-08-14T175654-967d4504
  local fail=0 out rc fixture

  fixture=$(mktemp -d)
  mkdir -p "$fixture/nodes"
  cat > "$fixture/events.jsonl" <<'EOF'
{"t":1786702828885,"ev":"start","limits":{"max_nodes":2,"node_timeout":10800,"max_retries":1}}
{"t":1786702828886,"ev":"spawn","run_id":"implementer~1"}
{"t":1786702828886,"ev":"spawn","run_id":"seal~1"}
{"t":1786702828888,"ev":"dispatch","run_id":"implementer~1","mode":"fresh","model":"dsv4flash/lingzhi","resume":false}
EOF
  # 用 epoch 而不是格式化时间串：`date -d '90 minutes ago' '+%F %T'` 打出的是本地时间，
  # touch 再解析一次，两边时区约定不一致就会把 mtime 推到未来（实测算出 -390 分钟，
  # 正好是 UTC+8 减 90 分钟）。@epoch 没有时区，绕开这一整类。
  local ts=$(( $(date +%s) - 5400 ))
  touch -d "@$ts" "$fixture/events.jsonl" "$fixture/nodes" "$fixture"

  echo "=== 反例：两面俱死（run target 与 workspace 都零写入 90 分钟；期望 STALLED，退出码 1）==="
  local dead_ws; dead_ws=$(mktemp -d); echo x > "$dead_ws/f.py"; touch -d "@$ts" "$dead_ws/f.py" "$dead_ws"
  echo "  run=$fixture ws=$dead_ws"
  out=$(verdict_for "$fixture" "$dead_ws"); rc=$?
  echo "  → $out"
  if [ "$rc" -eq 1 ] && [[ "$out" == STALLED* ]]; then echo "  ✅ 判定正确"; else echo "  ❌ 判定错误（rc=$rc）"; fail=1; fi
  rm -rf "$dead_ws"

  # 这一例专门钉死本探针犯过的那个错：run target 静默但 workspace 正在落码。
  # 旧实现只量 run target，会把它误判成 STALLED —— 实测 05 就是这个形态。
  echo "=== 关键例：run target 静默 90 分钟，但 workspace 刚写（期望 RUNNING，退出码 0）==="
  local live_ws; live_ws=$(mktemp -d); echo y > "$live_ws/server.py"   # mtime = 现在
  echo "  run=$fixture ws=$live_ws"
  out=$(verdict_for "$fixture" "$live_ws"); rc=$?
  echo "  → $out"
  if [ "$rc" -eq 0 ] && [[ "$out" == RUNNING* ]]; then
    echo "  ✅ 判定正确（旧实现会在这里误判 STALLED 并掐死一条正在成功的单）"
  else echo "  ❌ 判定错误（rc=$rc）—— 误杀回归了"; fail=1; fi
  rm -rf "$live_ws"
  rm -rf "$fixture"

  echo "=== 正例：已知正常收束的 run（期望 NORMAL / 退出码 0）==="
  echo "  $healthy_run"
  out=$(verdict_for "$healthy_run"); rc=$?
  echo "  → $out"
  if [ "$rc" -eq 0 ] && [[ "$out" == NORMAL* ]]; then echo "  ✅ 判定正确"; else echo "  ❌ 判定错误（rc=$rc）"; fail=1; fi

  echo "=== 自证结论 ==="
  if [ "$fail" -eq 0 ]; then
    echo "  ✅ 探针在三例（两面俱死 / 仅 workspace 活 / 已收束）上均判定正确 —— 非恒真非恒假"
    return 0
  fi
  echo "  ❌ 探针自证失败，判定不可信"
  return 1
}

case "${1:-}" in
  --self-test) self_test; exit $? ;;
  ""|-h|--help)
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit 2 ;;
esac

RUN_DIR="$1"; shift
while [ $# -gt 0 ]; do
  case "$1" in
    --minutes) STALL_MINUTES="${2:?--minutes 需要一个值}"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

out=$(verdict_for "$RUN_DIR"); rc=$?
echo "$out"
exit $rc
