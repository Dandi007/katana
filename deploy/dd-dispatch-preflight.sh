#!/usr/bin/env bash
# dd-dispatch-preflight.sh —— 派 dev-dispatch 单之前的确定性前置校验。
#
# **这不是容器部署件**，与 dd-stall-probe.sh 同属本卷 ops 工具面：只读、无副作用。
#
# ---------------------------------------------------------------------------
# 为什么它存在，以及为什么它是「挡得住」而不是「提醒一下」
# ---------------------------------------------------------------------------
# 选择器有**两层互不一致**的注册表，我连踩两次、烧掉两个 development id：
#   · dev_katana_search_wiring_03 用 dsv4pro/lingzhi
#     → 过 agent-run 的 model-registry，被 engine-frozen 解析器拒：
#       "Unknown selector: dsv4pro/lingzhi" → CONFIGURATION_MISMATCH 循环重试；
#       而 attempt-context/v1 **不支持 reconfigure**，只能换号重派。
#   · dev_katana_search_wiring_04 用 ds/lingzhi
#     → 过 engine-frozen 解析器，被 agent-run 拒：
#       'agent-run: selector "ds/lingzhi" 不在 model-registry 中' → state=FAILED。
#
# 教训写进 PR 说明是**挡不住**的——下次派单时没人会去读上一张单的 PR。所以把它做成
# 派单流程里绕不过去的一步：**initial_handoff 的 JSON 只从本脚本出**。选择器任一层
# 不认，脚本直接非零退出且**不打印 handoff**，那份 JSON 就不存在，也就没法粘进
# development_create。校验不通过 → 物理上拿不到入参，而不是「记得检查」。
#
# 顺带把 H0 的几条不变量一起验了（它们也都是踩过的）：worktree 必须干净、HEAD 的唯一
# 父必须是 target base、symbolic-ref 必须等于 durable MR 的 head_ref。
#
# 用法：
#   deploy/dd-dispatch-preflight.sh \
#       --development-id dev_xxx --worktree /abs/path --mr-id 135 \
#       --base-commit <40hex> --spec-revision-id specrev_xxx \
#       [--selector <role>=<selector> ...]
#   deploy/dd-dispatch-preflight.sh --self-test
#
# 退出码：0=全部通过（stdout 最后一行是 initial_handoff JSON） 1=校验不通过 2=用法错误
set -uo pipefail

FROZEN_RESOLVER=(/usr/bin/node --import
  /data/loop-engine/development-mcp/engine-frozen/scripts/register-node-esm-extension-loader.mjs
  /data/loop-engine/development-mcp/engine-frozen/dist/lib/model-resolver.js)
REGISTRY_JSON=/data/loop-engine/config/model-registry.json
REPO_REMOTE="https://github.com/Dandi007/katana.git"
MR_BASE_REF="refs/heads/release/katana-mcp-search-wiring"

err() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*" >&2; }

# --- 双层选择器校验（本脚本存在的首要理由）---------------------------------
check_selector() {
  local sel="$1" bad=0 out
  out=$("${FROZEN_RESOLVER[@]}" --json "$sel" 2>&1 | head -1)
  if printf '%s' "$out" | grep -q '^{'; then
    ok "frozen resolver 认得 $sel → $out"
  else
    err "frozen resolver 不认 $sel → $out"; bad=1
  fi
  if python3 -c "
import json,sys
d=json.load(open('$REGISTRY_JSON')).get('selectors',{})
sys.exit(0 if '$sel' in d else 1)" 2>/dev/null; then
    ok "agent-run registry 认得 $sel → $(python3 -c "
import json;print(json.load(open('$REGISTRY_JSON'))['selectors']['$sel']['model'])" 2>/dev/null)"
  else
    err "agent-run registry 不认 $sel（model-registry.json 里没有这个键）"; bad=1
  fi
  return $bad
}

self_test() {
  local fail=0
  echo "=== 反例 1：dsv4pro/lingzhi（03 烧号那个；registry 认、frozen 不认，期望拒绝）==="
  if check_selector dsv4pro/lingzhi; then echo "  ❌ 竟然放行了"; fail=1; else echo "  ✅ 已拒绝"; fi
  echo "=== 反例 2：ds/lingzhi（04 烧号那个；frozen 认、registry 不认，期望拒绝）==="
  if check_selector ds/lingzhi; then echo "  ❌ 竟然放行了"; fail=1; else echo "  ✅ 已拒绝"; fi
  echo "=== 正例：opus/lingzhi（两层都认，期望放行）==="
  if check_selector opus/lingzhi; then echo "  ✅ 已放行"; else echo "  ❌ 竟然拒绝了"; fail=1; fi
  echo "=== 自证结论 ==="
  if [ "$fail" -eq 0 ]; then
    echo "  ✅ 两个真实烧号的选择器都被挡下，已知可用的被放行 —— 非恒真非恒假"; return 0
  fi
  echo "  ❌ 自证失败"; return 1
}

[ "${1:-}" = "--self-test" ] && { self_test; exit $?; }

DEV_ID=""; WT=""; MR_ID=""; BASE=""; SPEC_REV=""; declare -a SELECTORS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --development-id) DEV_ID="$2"; shift 2 ;;
    --worktree) WT="$2"; shift 2 ;;
    --mr-id) MR_ID="$2"; shift 2 ;;
    --base-commit) BASE="$2"; shift 2 ;;
    --spec-revision-id) SPEC_REV="$2"; shift 2 ;;
    --selector) SELECTORS+=("$2"); shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DEV_ID" ] && [ -n "$WT" ] && [ -n "$MR_ID" ] && [ -n "$BASE" ] && [ -n "$SPEC_REV" ] || {
  echo "缺必需参数，见 $0 --help" >&2; exit 2; }

rc=0
echo "── 选择器双层校验 ──" >&2
if [ "${#SELECTORS[@]}" -eq 0 ]; then
  ok "未指定 --selector，使用 profile 默认（不校验）"
else
  for pair in "${SELECTORS[@]}"; do
    role="${pair%%=*}"; sel="${pair#*=}"
    echo "  role=$role selector=$sel" >&2
    check_selector "$sel" || rc=1
  done
fi

echo "── H0 不变量 ──" >&2
head_ref="refs/heads/loopdev/$DEV_ID/attempt-context-v1"
if [ -z "$(git -C "$WT" status --porcelain --untracked-files=all 2>/dev/null)" ]; then
  ok "worktree 干净"; else err "worktree 不干净"; rc=1; fi
actual_ref=$(git -C "$WT" symbolic-ref --quiet HEAD 2>/dev/null)
if [ "$actual_ref" = "$head_ref" ]; then ok "symbolic-ref = $head_ref"
else err "symbolic-ref 是 $actual_ref，期望 $head_ref"; rc=1; fi
parents=$(git -C "$WT" rev-list --parents -n1 HEAD 2>/dev/null)
h0=$(echo "$parents" | awk '{print $1}'); par=$(echo "$parents" | awk '{print $2}'); extra=$(echo "$parents" | awk '{print $3}')
if [ "$par" = "$BASE" ] && [ -z "$extra" ]; then ok "H0 $h0 的唯一父 = $BASE"
else err "H0 父不对：parents=$parents"; rc=1; fi

if [ "$rc" -ne 0 ]; then
  echo >&2
  err "前置校验未通过 —— **不输出 initial_handoff**。修好再来，别手工拼 JSON 绕过。"
  exit 1
fi
echo >&2; ok "全部通过，输出 initial_handoff"; echo >&2

DEV_ID="$DEV_ID" WT="$WT" MR_ID="$MR_ID" BASE="$BASE" SPEC_REV="$SPEC_REV" \
REMOTE="$REPO_REMOTE" BASEREF="$MR_BASE_REF" python3 - <<'PY'
import hashlib, json, os, subprocess
WT=os.environ["WT"]; DEV=os.environ["DEV_ID"]
def git(*a): return subprocess.run(["git","-C",WT,*a],capture_output=True,text=True,check=True).stdout.strip()
def ref(p):
    raw=subprocess.run(["git","-C",WT,"show",f"HEAD:{p}"],capture_output=True,check=True).stdout
    return {"path":p,"blob_oid":git("rev-parse",f"HEAD:{p}"),"digest":"sha256:"+hashlib.sha256(raw).hexdigest()}
r={"bootstrap_artifact":ref(".dev-dispatch/development.json"),
   "contract_version":"dev-dispatch.attempt-context/v1","development_id":DEV,
   "durable_mr":{"provider":"github","repository_owner":"Dandi007","repository_name":"katana",
     "mr_id":os.environ["MR_ID"],"mr_url":f"https://github.com/Dandi007/katana/pull/{os.environ['MR_ID']}",
     "head_ref":f"refs/heads/loopdev/{DEV}/attempt-context-v1","base_ref":os.environ["BASEREF"]},
   "feedback_ref":{**ref(".dev-dispatch/feedback/index.json"),"entry_count":0},
   "output_commit":git("rev-parse","HEAD"),"remote_url":os.environ["REMOTE"],
   "spec_ref":ref(".dev-dispatch/spec/approved.md"),"spec_revision_id":os.environ["SPEC_REV"],
   "target_base_commit":os.environ["BASE"]}
canon=json.dumps(r,sort_keys=True,ensure_ascii=False,separators=(",",":"))
print(json.dumps({**r,"bootstrap_receipt_digest":"sha256:"+hashlib.sha256(canon.encode()).hexdigest(),
                  "worktree_path":WT},ensure_ascii=False,separators=(",",":")))
PY
