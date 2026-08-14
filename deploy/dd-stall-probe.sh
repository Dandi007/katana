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
# 先看终局信号，再看停滞信号，顺序不能反（已收束的 run 同样「长时间零写入」）：
#   1. nodes/ 非空，或 events.jsonl 出现 "ev":"stop"  → NORMAL（已产出/已收束）
#   2. 否则：run target 目录零写入时长 ≥ STALL_MINUTES 且 nodes/ 为空 → STALLED
#   3. 否则                                                            → RUNNING
#
# STALL_MINUTES 默认 30，标定依据（全部为本机实测，非估计）：
#   · 健康 implementer 节点完成耗时 = 988s ≈ 16.5 分钟
#     （job-2026-08-14T175654-eec8a6e8 的 elapsed_ms=988405）
#   · 健康在跑作业的 workspace 写入间隔 = 2s / 94s / 1378s
#   · 01 卡死签名：零写入 72 分钟；02 卡死签名：零写入 30 分钟
#   取 30 分钟 ≈ 健康完成耗时的两倍，既不会误杀慢作业，也不必等满引擎的
#   node_timeout（10800s = 3 小时）。
#
# 用法：
#   deploy/dd-stall-probe.sh <run-target-dir> [--minutes N]
#   deploy/dd-stall-probe.sh --self-test        # 正反两例自证
#
# 退出码：0=NORMAL/RUNNING（不要处置） 1=STALLED（可处置） 2=用法或输入错误
set -uo pipefail

STALL_MINUTES="${KATANA_DD_STALL_MINUTES:-30}"

verdict_for() {
  # $1 = run target dir。回显一行 "<VERDICT> <说明>"，并 return 0/1。
  local R="$1"
  [ -d "$R" ] || { echo "INPUT_ERROR 目录不存在: $R"; return 2; }
  [ -f "$R/events.jsonl" ] || { echo "INPUT_ERROR 缺 events.jsonl: $R"; return 2; }

  local nodes stop now newest quiet_s quiet_m
  nodes=$(ls -A "$R/nodes" 2>/dev/null | wc -l)
  # grep -c 无命中时**既打印 0 又返回 1**，写成 `|| echo 0` 会得到 "0\n0"，
  # 后面的 [ "$stop" -gt 0 ] 直接 integer expected 报错。自证第一次跑就撞到了。
  stop=$(grep -c '"ev":"stop"' "$R/events.jsonl" 2>/dev/null); stop="${stop:-0}"

  # 1) 终局信号优先：已收束的 run 也会长时间零写入，先判它否则必然误报
  if [ "$nodes" -gt 0 ] || [ "$stop" -gt 0 ]; then
    echo "NORMAL nodes=$nodes stop_events=$stop（已产出或已收束）"
    return 0
  fi

  now=$(date +%s)
  newest=$(find "$R" -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1 | cut -d. -f1)
  [ -z "$newest" ] && { echo "INPUT_ERROR 目录内无文件: $R"; return 2; }
  quiet_s=$(( now - newest ))
  quiet_m=$(( quiet_s / 60 ))

  if [ "$quiet_m" -ge "$STALL_MINUTES" ]; then
    echo "STALLED 零写入 ${quiet_m} 分钟（阈值 ${STALL_MINUTES}）nodes=0 stop=0 最近写入=$(date -d @"$newest" '+%H:%M:%S')"
    return 1
  fi
  echo "RUNNING 零写入 ${quiet_m} 分钟（未达阈值 ${STALL_MINUTES}）nodes=0 stop=0"
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

  echo "=== 反例：合成的卡死签名（events 止于 dispatch / nodes 空 / 零写入 90 分钟；期望 STALLED，退出码 1）==="
  echo "  $fixture"
  out=$(verdict_for "$fixture"); rc=$?
  echo "  → $out"
  if [ "$rc" -eq 1 ] && [[ "$out" == STALLED* ]]; then echo "  ✅ 判定正确"; else echo "  ❌ 判定错误（rc=$rc）"; fail=1; fi
  rm -rf "$fixture"

  echo "=== 正例：已知正常收束的 run（期望 NORMAL / 退出码 0）==="
  echo "  $healthy_run"
  out=$(verdict_for "$healthy_run"); rc=$?
  echo "  → $out"
  if [ "$rc" -eq 0 ] && [[ "$out" == NORMAL* ]]; then echo "  ✅ 判定正确"; else echo "  ❌ 判定错误（rc=$rc）"; fail=1; fi

  echo "=== 自证结论 ==="
  if [ "$fail" -eq 0 ]; then
    echo "  ✅ 探针在正反两例上均判定正确 —— 它不是恒真也不是恒假"
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
