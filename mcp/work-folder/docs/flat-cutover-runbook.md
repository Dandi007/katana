# Work Folder Flat Cutover Runbook

Work Folder flat cutover 必须在服务离线、Git HEAD 锁定且 repair metadata 完整时执行；迁移完成但尚未通过 `verify` 和 server startup gate 前，不得提交或恢复流量。

## 操作摘要

- `inventory` 只盘点，不修改；结构异常、双根碰撞、symlink、特殊文件、非法 tombstone 或未分类 payload 会 fail closed。
- `plan` 把 topic、ID、内容 hash、control archive、binary/text API 可达性和 Git diff 冻结为一个 content-addressed artifact；缺失或损坏的 `_brief.md` 必须显式 repair。
- `sentinel` 生成与 plan hash、source HEAD、repo root 绑定的外部 maintenance 文件。
- `apply` 只执行 frozen plan，不 commit；每一步写入 repo 外 checkpoint，中断后只能恢复同一个 plan。
- `verify` 同时核对文件 hash、control archive、Git diff、ID/tombstone、INDEX，以及每个文件经 `fs_read` 或 `fs_read_bytes` 的可达性。

## 1. 前置条件

Cutover 开始前必须同时满足：

1. Work Folder MCP 已停止接收读写流量，且不会有其它进程修改 data repo。
2. data repo 是明确指定的 Git toplevel，working tree clean。
3. `legacy_root` 是 data repo 内的旧日期树根。
4. plan、repair、sentinel、checkpoint 和命令输出均存放在 data repo 之外。
5. 已备份 source HEAD，且有独立方式恢复整个 repo；迁移器本身不会 reset、clean、commit 或 push。
6. 所有迁移 phase 都使用下方精确 `PYTHONPATH`，不追加 ambient
   `PYTHONPATH`。迁移器还会用 `inspect.getfile` 验证 kernel、shared 和
   work-folder package 全部来自 `KATANA_CODE` 所指向的同一 checkout；
   任何预加载的其它安装都会 fail closed。

以下示例变量只用于说明，必须替换成实际绝对路径：

```bash
KATANA_CODE=/absolute/path/to/katana-code-checkout
REPO=/absolute/path/to/work-folder-data
LEGACY=/absolute/path/to/work-folder-data/智元工作/工作记录
STATE=/absolute/path/outside-repo/work-folder-cutover
mkdir -p "$STATE"
```

## 2. Inventory 与 repair gate

先生成 inventory：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$KATANA_CODE/mcp/kernel:$KATANA_CODE/mcp/shared:$KATANA_CODE/mcp/work-folder" \
python3 "$KATANA_CODE/mcp/work-folder/scripts/migrate_flat.py" inventory \
  --repo-root "$REPO" \
  --legacy-root "$LEGACY" \
  --output "$STATE/inventory.json"
```

必须人工审阅：

- `errors` 为空且 `ok=true`。
- primary root 与 accidental `智元工作/工作记录` double root 没有相同 logical locator。
- `is_empty=true`、`brief_state=missing|parse_error|invalid_metadata` 的 topic 全部进入 repair 清单。
- tombstone 集合完整，且没有 live ID 与 tombstone 重叠。
- 路径任意层级的 `.superpowers`、`.review-loop`、`.sessions` segment 都会迁往 folder 内的 `archive/runtime/<type>/`；segment 前后的完整相对上下文会保留，以避免 nested run 重名。
- text 文件标为 `fs_read`，binary 文件标为 `fs_read_bytes`。

迁移器不会猜测 metadata。每个 repair entry 必须绑定 inventory 中的 state；非 missing brief 还必须绑定原始 SHA-256：

```json
{
  "repairs": {
    "2026/07/15/example-topic": {
      "state": "missing",
      "expected_sha256": null,
      "brief_text": "---\ntitle: Example\nstatus: active\ncreated: 2026-07-15\nupdated: 2026-07-29\n---\n\n**Goal:** 明确且可验证的目标\n"
    }
  }
}
```

## 3. 冻结 plan 与 maintenance sentinel

生成 plan：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$KATANA_CODE/mcp/kernel:$KATANA_CODE/mcp/shared:$KATANA_CODE/mcp/work-folder" \
python3 "$KATANA_CODE/mcp/work-folder/scripts/migrate_flat.py" plan \
  --repo-root "$REPO" \
  --legacy-root "$LEGACY" \
  --inventory "$STATE/inventory.json" \
  --repairs "$STATE/repairs.json" \
  --output "$STATE/plan.json"
```

审阅 plan 后记录 `source_head`、`inventory_hash`、`plan_hash`、topic 数、control actions 和 `expected_diff_paths`。审批人必须把通过的 `plan_hash` 写入独立只读审批渠道；执行人从该渠道复制到 `APPROVED_PLAN_HASH`，不得从待执行的 `plan.json` 自取或重算。

只有维护控制面已经阻止新 writer、在途 mutation 已排空并保存停服证据后，才可生成外部 sentinel。`apply` 还会在完整 gate→move→verify 区间持有 server 使用的同一 repository mutation lock；sentinel 本身不替代停服证据。

```bash
APPROVED_PLAN_HASH='<从独立只读审批记录复制>'

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$KATANA_CODE/mcp/kernel:$KATANA_CODE/mcp/shared:$KATANA_CODE/mcp/work-folder" \
python3 "$KATANA_CODE/mcp/work-folder/scripts/migrate_flat.py" sentinel \
  --repo-root "$REPO" \
  --legacy-root "$LEGACY" \
  --plan "$STATE/plan.json" \
  --expected-plan-hash "$APPROVED_PLAN_HASH" \
  --output "$STATE/maintenance.json"
```

先做无副作用 gate 检查：

```bash
HEAD=$(git -C "$REPO" rev-parse HEAD)

PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$KATANA_CODE/mcp/kernel:$KATANA_CODE/mcp/shared:$KATANA_CODE/mcp/work-folder" \
python3 "$KATANA_CODE/mcp/work-folder/scripts/migrate_flat.py" apply \
  --repo-root "$REPO" \
  --legacy-root "$LEGACY" \
  --plan "$STATE/plan.json" \
  --expected-head "$HEAD" \
  --expected-plan-hash "$APPROVED_PLAN_HASH" \
  --maintenance-sentinel "$STATE/maintenance.json" \
  --checkpoint "$STATE/checkpoint.json" \
  --dry-run \
  --output "$STATE/dry-run.json"
```

## 4. Apply、resume 与 verify

Dry-run 通过后执行 apply：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$KATANA_CODE/mcp/kernel:$KATANA_CODE/mcp/shared:$KATANA_CODE/mcp/work-folder" \
python3 "$KATANA_CODE/mcp/work-folder/scripts/migrate_flat.py" apply \
  --repo-root "$REPO" \
  --legacy-root "$LEGACY" \
  --plan "$STATE/plan.json" \
  --expected-head "$HEAD" \
  --expected-plan-hash "$APPROVED_PLAN_HASH" \
  --maintenance-sentinel "$STATE/maintenance.json" \
  --checkpoint "$STATE/checkpoint.json" \
  --output "$STATE/apply-result.json"
```

Apply 中断时，不生成新 inventory/plan，也不手工猜测已完成步骤。排除磁盘或权限故障后，原样重跑同一条命令；checkpoint 会验证 repo、HEAD、plan hash、已完成 destination 和当前 diff，再继续未完成动作。checkpoint 不匹配、丢失或损坏时停止，保留现场供审计。

Apply 成功会内嵌 verification，仍应独立再跑一次：

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$KATANA_CODE/mcp/kernel:$KATANA_CODE/mcp/shared:$KATANA_CODE/mcp/work-folder" \
python3 "$KATANA_CODE/mcp/work-folder/scripts/migrate_flat.py" verify \
  --repo-root "$REPO" \
  --legacy-root "$LEGACY" \
  --plan "$STATE/plan.json" \
  --output "$STATE/verify-result.json"
```

只有以下条件全部满足才可进入提交：

- `ok=true`、`source_anchor_count=0`、`ids_unique=true`。
- `unexpected_diff_paths=[]`、`missing_diff_paths=[]`。
- `api_reachability.fs_read + api_reachability.fs_read_bytes` 等于所有 migrated files 数。
- `.katana/manifests/` 不存在；旧 manifest 位于 `.katana/legacy-manifests/`，并与 `.katana/legacy-manifest-inventory.json` 一一对应。
- root 只保留 `wf-*` folders、`INDEX.md`、`.gitignore`、可选 `.gitkeep`、`.git` 和受治理 `.katana` control state。

## 5. Commit 与恢复服务

先人工审阅 `git status`、未跟踪文件和完整 diff。随后逐项对照 plan 的
`expected_diff_paths` 精确执行 `git add -- <path>`，不得使用 broad
`git add -A`；确认 cached path 集合与 plan 完全一致后，把整个 cutover
作为单一 data migration commit：

```bash
git -C "$REPO" status --short
git -C "$REPO" diff --stat HEAD
git -C "$REPO" ls-files --others --exclude-standard
git -C "$REPO" diff --cached --name-status
git -C "$REPO" commit -m "migrate(work-folder): cut over to flat ID layout"
```

不要使用 broad clean/reset。提交后启动 Work Folder MCP。Server startup 会再次检查 flat canary、tombstone ledger、legacy manifest inventory/archive、root topology、INDEX、Git clean state、runtime SQLite ledger 与 Git receipt reconciliation。Startup gate 失败时保持 maintenance，不恢复流量。

恢复流量后做最小 smoke：

1. `wf_list` / `wf_search` 只返回 opaque `folder_id`。
2. `wf_search` 必须把 configured data root 及其 deterministic `source_id`
   作为 exact filter 传给 vault-search；source filter 在全局 `top_k` 截断前生效，
   避免其他 source 的高分结果挤掉 Work Folder 命中。
3. source filter 是主隔离边界，但 server 仍须校验返回 locator 为
   `wf-<6 lowercase hex>/<folder-relative filename>`；查询 backend 时按固定倍数
   oversample candidates，过滤同源控制文件后再截断至用户 `top_k`，且不把
   backend 输出直接暴露。
4. 对一个 text 文件调用 `fs_read(folder_id, filename)`。
5. 对一个 binary 文件调用 `fs_read_bytes(folder_id, filename, limit=1)`。
6. 用新 idempotency key 做一次受治理 mutation，并确认 Git commit、runtime receipt 和 replay。

## 6. Failure policy

- **Apply 前失败**：修正 source 数据或显式 repair 后重新 inventory 和 plan。
- **Apply 中失败且 checkpoint 有效**：只恢复同一 plan。
- **Apply 中失败且 checkpoint 不可信**：停止；从独立备份恢复到 source HEAD，重新开始，不在 partial tree 上生成新 plan。
- **Verify 或 startup 失败**：保持离线，保存 plan、checkpoint、Git diff 和错误输出。
- **Commit 后失败**：不要让旧日期树与 flat tree 同时服务；按组织的数据恢复流程回到完整 pre-cutover snapshot。

# References

- `mcp/work-folder/scripts/migrate_flat.py` — inventory / plan / sentinel / apply / verify 的 executable contract。
- `mcp/work-folder/katana_work_folder_mcp/server.py` — flat topology、runtime ledger、legacy manifest inventory 与 startup reconciliation gate。
- `mcp/work-folder/tests/test_migrate_flat.py` — double root、empty topic、tombstone、binary API、checkpoint resume 与 startup acceptance tests。
- `mcp/work-folder/tests/test_server.py` — server fail-closed startup tests。
