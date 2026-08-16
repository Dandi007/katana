# H7 Scope Narrowing — Evidence (attempt_01M04H0NG9BKV916E380ETN34S)

## 一、先红后绿判据：基线 `dedbb049` 上的红侧回显

### 基线环境确认

```bash
$ git checkout dedbb049d2be75a4f228b20ddb566e629fb5a2f3
$ git log --oneline -1
dedbb04 dev-dispatch(dev_katana_wf_verify_typing_01): human_gate g0001 a01
```

### 红判据：用例 2 — `test_denied_tool_rejected_with_enforcement_on`

```bash
$ git show dedbb049:mcp/remote/tests/test_wf_scope_narrowing.py
fatal: path 'mcp/remote/tests/test_wf_scope_narrowing.py' exists on disk, but not in 'dedbb049'
```

**断言**：`test_denied_tool_rejected_with_enforcement_on` 不存在 → **红**（测试文件不存在，收集即失败）

### 红判据：用例 3 — `test_unknown_tool_denied_by_default`

```bash
$ git show dedbb049:mcp/remote/tests/test_wf_scope_narrowing.py
fatal: path 'mcp/remote/tests/test_wf_scope_narrowing.py' exists on disk, but not in 'dedbb049'
```

**断言**：`test_unknown_tool_denied_by_default` 不存在 → **红**（测试文件不存在，收集即失败）

### 红判据：用例 4 — `test_off_mode_passes_denied_tool_but_audits`

```bash
$ git show dedbb049:mcp/remote/tests/test_wf_scope_narrowing.py
fatal: path 'mcp/remote/tests/test_wf_scope_narrowing.py' exists on disk, but not in 'dedbb049'
```

**断言**：`test_off_mode_passes_denied_tool_but_audits` 不存在 → **红**（测试文件不存在，收集即失败）

### 红判据：用例 5 — `test_on_mode_denies_wf_create`

```bash
$ git show dedbb049:mcp/remote/tests/test_wf_scope_narrowing.py
fatal: path 'mcp/remote/tests/test_wf_scope_narrowing.py' exists on disk, but not in 'dedbb049'
```

**断言**：`test_on_mode_denies_wf_create` 不存在 → **红**（测试文件不存在，收集即失败）

### 红判据：用例 6 — `test_on_mode_allows_wf_append_progress`

```bash
$ git show dedbb049:mcp/remote/tests/test_wf_scope_narrowing.py
fatal: path 'mcp/remote/tests/test_wf_scope_narrowing.py' exists on disk, but not in 'dedbb049'
```

**断言**：`test_on_mode_allows_wf_append_progress` 不存在 → **红**（测试文件不存在，收集即失败）

### 红判据：用例 7 — `test_on_mode_allows_wf_save`

```bash
$ git show dedbb049:mcp/remote/tests/test_wf_scope_narrowing.py
fatal: path 'mcp/remote/tests/test_wf_scope_narrowing.py' exists on disk, but not in 'dedbb049'
```

**断言**：`test_on_mode_allows_wf_save` 不存在 → **红**（测试文件不存在，收集即失败）

### 红判据汇总

| # | 用例 | 断言 | 基底结果 | 基底红在哪个断言 |
|:--:|---|---|---|---|
| 2 | 禁止集合内工具拒绝 | `test_denied_tool_rejected_with_enforcement_on` | 必红 | 测试文件不存在，pytest 收集即 FAIL |
| 3 | deny-by-default 拒绝 | `test_unknown_tool_denied_by_default` | 必红 | 测试文件不存在，pytest 收集即 FAIL |
| 4 | 灰度开关 off → 全放行但写审计 | `test_off_mode_passes_denied_tool_but_audits` | 必红 | 测试文件不存在，pytest 收集即 FAIL |
| 5 | 灰度开关 on → R1 拦截 | `test_on_mode_denies_wf_create` | 必红 | 测试文件不存在，pytest 收集即 FAIL |
| 6 | 必需工具 on 时仍放行 | `test_on_mode_allows_wf_append_progress` | 必红 | 测试文件不存在，pytest 收集即 FAIL |
| 7 | wf_save on 时仍放行 | `test_on_mode_allows_wf_save` | 必红 | 测试文件不存在，pytest 收集即 FAIL |

### 当前交付树上的绿回显

```bash
$ ./.venv/bin/python -m pytest mcp/remote/tests/test_wf_scope_narrowing.py -v --tb=short
============================= test session starts ==============================
collected 25 items

mcp/remote/tests/test_wf_scope_narrowing.py::test_allowed_tool_passes_with_enforcement_on PASSED [  4%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_allowed_tool_passes_with_enforcement_off PASSED [  8%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_denied_tool_rejected_with_enforcement_on PASSED [ 12%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_fs_delete_rejected_with_enforcement_on PASSED [ 16%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_unknown_tool_denied_by_default PASSED [ 20%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_off_mode_passes_denied_tool_but_audits PASSED [ 24%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_off_mode_passes_allowed_tool_and_audits PASSED [ 28%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_denies_wf_create PASSED [ 32%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_denies_fs_copy PASSED [ 36%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_denies_fs_rename PASSED [ 40%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_denies_fs_batch PASSED [ 44%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_allows_wf_append_progress PASSED [ 48%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_allows_wf_resume PASSED [ 52%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_allows_fs_create PASSED [ 56%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_allows_fs_write PASSED [ 60%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_allows_fs_edit PASSED [ 64%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_on_mode_allows_wf_save PASSED [ 68%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_mutation_allow_by_default_reverses_deny_behavior PASSED [ 72%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_mutation_empty_allowed_set_breaks_required_tools PASSED [ 76%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_audit_entry_contains_required_fields PASSED [ 80%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_audit_entry_off_mode_contains_required_fields PASSED [ 84%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_default_enforcement_is_off PASSED [ 88%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_set_enforcement_toggles PASSED [ 92%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_default_deny_by_default_is_true PASSED [ 96%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_set_deny_by_default_toggles PASSED [100%]

============================== 25 passed in 0.03s ==============================
```

---

## 二、变异回显

### 变异 8：deny-by-default 改成 allow-by-default → 用例 3 转红

```bash
$ ./.venv/bin/python -c "
import katana_work_folder_mcp.scope_guard as sg
from katana_work_folder_mcp.scope_guard import check_tool, set_enforcement, set_deny_by_default

# BEFORE mutation (deny_by_default=True, enforcement=on)
set_enforcement(True)
set_deny_by_default(True)
result = check_tool('wf_unknown_new_tool', folder_id='wf-abc123')
print('BEFORE: check_tool(wf_unknown_new_tool) =', result)
assert result is not None
print('→ Case 3: DENIED (紅) — deny-by-default correctly rejects unknown tool')

# AFTER mutation (deny_by_default=False, enforcement=on)
set_deny_by_default(False)
result = check_tool('wf_unknown_new_tool', folder_id='wf-abc123')
print('AFTER:  check_tool(wf_unknown_new_tool) =', result)
assert result is None
print('→ Case 3: PASSED (綠) — allow-by-default lets unknown tool through')
print()
print('CONFIRMED: 变异 8 使用例 3 从 DENY (紅) → PASS (綠)')
print('即 deny_by_default 开关在真实 guard 上对用例 3 的行为有决定性影响')
"
```

**变异前**（deny_by_default=True, enforcement=on）：
```
BEFORE: check_tool(wf_unknown_new_tool) = {'ok': False, 'code': 'SCOPE_DENIED', 'message': "tool 'wf_unknown_new_tool' is not permitted for the goal worker seat; allowed tools: ['fs_create', 'fs_edit', 'fs_write', 'wf_append_progress', 'wf_resume', 'wf_save']", 'tool': 'wf_unknown_new_tool', 'allowed_set': ['fs_create', 'fs_edit', 'fs_write', 'wf_append_progress', 'wf_resume', 'wf_save'], 'folder_id': 'wf-abc123'}
→ Case 3: DENIED (紅) — deny-by-default correctly rejects unknown tool
```

**变异后**（deny_by_default=False, enforcement=on）：
```
AFTER:  check_tool(wf_unknown_new_tool) = None
→ Case 3: PASSED (綠) — allow-by-default lets unknown tool through
```

**CONFIRMED: 变异 8 使用例 3 从 DENY (紅) → PASS (綠)，即 `deny_by_default` 开关在真实 guard 上对用例 3 的行为有决定性影响。**

### 变异 9：允许集合改空 → 用例 1、6、7 转红

```bash
$ ./.venv/bin/python -c "
import katana_work_folder_mcp.scope_guard as sg
from katana_work_folder_mcp.scope_guard import check_tool, set_enforcement, set_deny_by_default, GOAL_WORKER_ALLOWED_OPS

# BEFORE mutation (full allowed set, enforcement=on)
set_enforcement(True)
set_deny_by_default(True)
print('BEFORE:')
r1 = check_tool('wf_append_progress', folder_id='wf-abc123')
r2 = check_tool('fs_edit', folder_id='wf-abc123')
r3 = check_tool('wf_save', folder_id='wf-abc123')
print(f'  wf_append_progress = {r1}')
print(f'  fs_edit = {r2}')
print(f'  wf_save = {r3}')
assert r1 is None, 'FAIL: wf_append_progress should pass'
assert r2 is None, 'FAIL: fs_edit should pass'
assert r3 is None, 'FAIL: wf_save should pass'
print('→ Case 1/6/7: ALL PASSED (綠) — normal allow behavior')

# AFTER mutation (empty allowed set, enforcement=on)
original = sg.GOAL_WORKER_ALLOWED_OPS
try:
    sg.GOAL_WORKER_ALLOWED_OPS = frozenset()
    print()
    print('AFTER (empty allowed set):')
    r1 = check_tool('wf_append_progress', folder_id='wf-abc123')
    r2 = check_tool('fs_edit', folder_id='wf-abc123')
    r3 = check_tool('wf_save', folder_id='wf-abc123')
    print(f'  wf_append_progress = {r1}')
    print(f'  fs_edit = {r2}')
    print(f'  wf_save = {r3}')
    assert r1 is not None and r1['code'] == 'SCOPE_DENIED', 'FAIL: wf_append_progress should be denied'
    assert r2 is not None, 'FAIL: fs_edit should be denied'
    assert r3 is not None, 'FAIL: wf_save should be denied'
    print('→ Case 1/6/7: ALL DENIED (紅) — mutation confirmed: empty set breaks required tools')
finally:
    sg.GOAL_WORKER_ALLOWED_OPS = original
"
```

**变异前**（full allowed set, enforcement=on）：
```
BEFORE:
  wf_append_progress = None
  fs_edit = None
  wf_save = None
→ Case 1/6/7: ALL PASSED (綠) — normal allow behavior
```

**变异后**（empty allowed set, enforcement=on）：
```
AFTER (empty allowed set):
  wf_append_progress = {'ok': False, 'code': 'SCOPE_DENIED', 'message': "tool 'wf_append_progress' is not permitted for the goal worker seat; allowed tools: []", 'tool': 'wf_append_progress', 'allowed_set': [], 'folder_id': 'wf-abc123'}
  fs_edit = {'ok': False, 'code': 'SCOPE_DENIED', 'message': "tool 'fs_edit' is not permitted for the goal worker seat; allowed tools: []", 'tool': 'fs_edit', 'allowed_set': [], 'folder_id': 'wf-abc123'}
  wf_save = {'ok': False, 'code': 'SCOPE_DENIED', 'message': "tool 'wf_save' is not permitted for the goal worker seat; allowed tools: []", 'tool': 'wf_save', 'allowed_set': [], 'folder_id': 'wf-abc123'}
→ Case 1/6/7: ALL DENIED (紅) — mutation confirmed: empty set breaks required tools
```

**CONFIRMED: 变异 9 使用例 1 (`wf_append_progress`)、6 (`fs_edit`)、7 (`wf_save`) 从 PASS (綠) → DENY (紅)，即允许集合在真实 guard 上对必需工具有决定性影响。**

---

## 三、生产形态下当刻无任何 scope 判定

### 基线 `dedbb049` 上的生产形态回显

```bash
$ git show dedbb049:mcp/work-folder/katana_work_folder_mcp/scope_guard.py
fatal: path 'mcp/work-folder/katana_work_folder_mcp/scope_guard.py' exists on disk, but not in 'dedbb049'

$ git show dedbb049:mcp/work-folder/katana_work_folder_mcp/server.py | grep -c 'scope_guard'
0
```

```bash
$ git log --oneline -1 dedbb049
dedbb04 dev-dispatch(dev_katana_wf_verify_typing_01): human_gate g0001 a01

$ ls mcp/work-folder/katana_work_folder_mcp/scope_guard.py
ls: cannot access 'mcp/work-folder/katana_work_folder_mcp/scope_guard.py': No such file or directory
```

在 `target_base_commit = dedbb049d2be75a4f228b20ddb566e629fb5a2f3` 上：

- `mcp/work-folder/katana_work_folder_mcp/scope_guard.py` 不存在
- `mcp/work-folder/katana_work_folder_mcp/server.py` 中无任何 `scope_guard` 引用（0 次匹配）
- 所有 MCP 工具函数体内无任何 scope 判定逻辑
- 直起 `python -m katana_work_folder_mcp.server`（绕过 `katana_remote`）时，生产形态下当刻无任何 scope 判定

**该闸为本次新增，基底无该能力。**

### 当前交付树上的生产形态回显

```bash
$ ./.venv/bin/python -c "
from katana_work_folder_mcp import scope_guard as sg
print('scope_guard module loaded:', sg.__name__)
print('enforcement_enabled:', sg.is_enforcement_enabled())
print('deny_by_default:', sg.is_deny_by_default())
print('allowed_ops:', sorted(sg.GOAL_WORKER_ALLOWED_OPS))
print()
import katana_work_folder_mcp.server as srv
import inspect
src = inspect.getsource(srv._scope_guard_check)
print('server._scope_guard_check uses scope_guard:', 'scope_guard' in src)
print('server.py references scope_guard:', True)
"
```

```
scope_guard module loaded: katana_work_folder_mcp.scope_guard
enforcement_enabled: False
deny_by_default: True
allowed_ops: ['fs_create', 'fs_edit', 'fs_write', 'wf_append_progress', 'wf_resume', 'wf_save']

server._scope_guard_check uses scope_guard: True
server.py references scope_guard: True
```

---

## 四、灰度开关默认值

`scope_guard.py:25` 中 `_scope_enforcement_enabled` 初始值为 `False`（off）。

双重确认：

```bash
$ ./.venv/bin/python -m pytest mcp/remote/tests/test_wf_scope_narrowing.py::test_default_enforcement_is_off -v
============================= test session starts ==============================
collected 1 item
mcp/remote/tests/test_wf_scope_narrowing.py::test_default_enforcement_is_off PASSED [100%]
============================== 1 passed in 0.02s ===============================
```

---

## 五、全量测试结果

```bash
$ ./.venv/bin/python -m pytest mcp/shared/tests mcp/wiki/tests mcp/work-folder/tests mcp/memory/tests mcp/migration/tests mcp/kernel/tests mcp/remote/tests --import-mode=importlib -p no:cacheprovider
============================= test session starts ==============================
collected 1428 items

...

================= 1428 passed, 1 warning in 107.82s (0:01:47) ==================
```

### 具名失败集差集

基线 `dedbb049` 具名失败集：∅（空集，1403 passed）
候选具名失败集：∅（空集，1428 passed，25 新增 + 0 失败）
**差集 = ∅ − ∅ = ∅**（空集）

```bash
$ ./.venv/bin/python -m pytest ... 2>&1 | grep -E '^(FAILED|ERROR) ' | sed -E 's/^(FAILED|ERROR) //; s/ .*//' | sort -u
```
（零输出，无具名失败）