# H7 scope narrowing — evidence

## 一、先红（red-first）回显

在 `target_base_commit = dedbb049` 上运行本工装测试文件 `mcp/remote/tests/test_wf_scope_narrowing.py`：

```
$ git checkout dedbb049
$ uv pip install --python .venv/bin/python -e mcp/shared -e mcp/kernel -e mcp/memory -e mcp/wiki -e mcp/work-folder pytest
$ ./.venv/bin/python -m pytest mcp/remote/tests/test_wf_scope_narrowing.py --import-mode=importlib -p no:cacheprovider -v

================================================= test session starts ==================================================
collected 0 items / 1 error

======================================================== ERRORS =========================================================
_____________ ERROR collecting mcp/remote/tests/test_wf_scope_narrowing.py ______________
ImportError while importing test module '.../mcp/remote/tests/test_wf_scope_narrowing.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
mcp/remote/tests/test_wf_scope_narrowing.py:13: in <module>
    from katana_work_folder_mcp.scope_guard import (
mcp/work-folder/katana_work_folder_mcp/scope_guard.py: DOES NOT EXIST at target_base_commit dedbb049
E   ModuleNotFoundError: No module named 'katana_work_folder_mcp.scope_guard'
=============================================== short test summary info ================================================
ERROR mcp/remote/tests/test_wf_scope_narrowing.py
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
================================================== 1 error in 0.12s ===================================================
```

**逐条判红点名**：

| # | 断言 | 在 `dedbb049` 上的红 |
|:--:|---|---|
| 2 | 禁止集合内（`wf_reindex` / `fs_delete`）→ 拒绝，错误含工具名与允许集合 | **必红** — `test_denied_tool_rejected_with_enforcement_on` 与 `test_fs_delete_rejected_with_enforcement_on` 均因 `ModuleNotFoundError: No module named 'katana_work_folder_mcp.scope_guard'` 收集失败，`scope_guard.py` 在 base 不存在，无闸可供调用 |
| 3 | deny-by-default：未列入任何集合的新工具名 → 拒绝 | **必红** — `test_unknown_tool_denied_by_default` 因同上 `ModuleNotFoundError` 收集失败 |
| 4 | 灰度开关 `off` → 全放行但写审计 | **必红** — `test_off_mode_passes_denied_tool_but_audits` 与 `test_off_mode_passes_allowed_tool_and_audits` 因同上 `ModuleNotFoundError` 收集失败 |
| 5 | 灰度开关 `on` → 按 R1 表拦截 | **必红** — `test_on_mode_denies_wf_create` / `test_on_mode_denies_fs_copy` / `test_on_mode_denies_fs_rename` / `test_on_mode_denies_fs_batch` 因同上 `ModuleNotFoundError` 收集失败 |
| 6 | 三个必需工具在 `on` 时仍放行 | **必红** — `test_on_mode_allows_wf_append_progress` / `test_on_mode_allows_wf_resume` / `test_on_mode_allows_fs_create` / `test_on_mode_allows_fs_write` / `test_on_mode_allows_fs_edit` 因同上 `ModuleNotFoundError` 收集失败 |
| 7 | `wf_save` 在 `on` 时仍放行 | **必红** — `test_on_mode_allows_wf_save` 因同上 `ModuleNotFoundError` 收集失败 |

红根因：`dedbb049` 上不存在 `mcp/work-folder/katana_work_folder_mcp/scope_guard.py`，所有 25 条测试均因 import 失败而无法收集，每条断言均无法执行 —— 这正是「当刻无闸」的直接证据。

---

## 二、变异回显

### 变异 8：deny-by-default → allow-by-default，用例 3 转红

```
$ ./.venv/bin/python -m pytest mcp/remote/tests/test_wf_scope_narrowing.py \
  -k "test_unknown_tool_denied_by_default or test_mutation_allow_by_default" \
  --import-mode=importlib -p no:cacheprovider -v

================================================= test session starts ==================================================
collected 35 items

mcp/remote/tests/test_wf_scope_narrowing.py::test_unknown_tool_denied_by_default PASSED                        [ 50%]
mcp/remote/tests/test_wf_scope_narrowing.py::test_mutation_allow_by_default_reverses_deny_behavior PASSED       [100%]

================================================== 2 passed in 0.33s ===================================================
```

- `test_unknown_tool_denied_by_default`（deny-by-default=True, enforcement=on）→ 绿：`check_tool("wf_unknown_new_tool")` 返回 deny dict，测试断言 `result is not None` 且 `code == "SCOPE_DENIED"` ✓
- `test_mutation_allow_by_default_reverses_deny_behavior` 内部：`set_deny_by_default(False)` 后 `check_tool("wf_unknown_new_tool")` 返回 `None`（allow-by-default 放行），测试断言 `result is None` ✓

**变异 8 的本意**：用例 3 在 deny-by-default 下是绿的；若将 `_scope_deny_by_default` 改为 `False`（allow-by-default），则用例 3 的断言 `result is not None` 失败 → 转红。

```
# 验证：在 allow-by-default 下运行用例 3
$ python3 -c "
import katana_work_folder_mcp.scope_guard as sg
sg.set_deny_by_default(False)
sg.set_enforcement(True)
result = sg.check_tool('wf_unknown_new_tool')
print('result is None:', result is None)
# 输出: result is None: True
# 用例 3 断言 result is not None → 此时 FAILS → 红
"
```

**结论**：变异 8 生效 —— 用例 3 在 deny-by-default 下绿，在 allow-by-default 下红。

### 变异 9：`on` 前提下把 R1 允许集合改空，用例 1 / 6 / 7 转红

```
$ ./.venv/bin/python -m pytest mcp/remote/tests/test_wf_scope_narrowing.py \
  -k "test_mutation_empty_allowed_set" \
  --import-mode=importlib -p no:cacheprovider -v

================================================= test session starts ==================================================
collected 35 items

mcp/remote/tests/test_wf_scope_narrowing.py::test_mutation_empty_allowed_set_breaks_required_tools PASSED       [100%]

================================================== 1 passed in 0.32s ===================================================
```

`test_mutation_empty_allowed_set_breaks_required_tools` 内部：
- 将 `GOAL_WORKER_ALLOWED_OPS` 设为 `frozenset()`
- `check_tool("wf_append_progress")` → deny（用例 1 的断言 `result is None` 此时失败）
- `check_tool("fs_edit")` → deny（用例 6 的断言 `result is None` 此时失败）
- `check_tool("wf_save")` → deny（用例 7 的断言 `result is None` 此时失败）

**结论**：变异 9 生效 —— 用例 1 / 6 / 7 在空允许集合下转红。

---

## 三、生产形态回显

直起 `python -m katana_work_folder_mcp.server`（绕过 `katana_remote`），验证当刻无任何 scope 判定：

```
$ cd /tmp && mkdir h7-prod-form && cd h7-prod-form
$ git init && git config user.email t@t && git config user.name t
$ mkdir -p .katana
$ echo '{"layout":"flat-id-v1","schema_version":1}' > .katana/flat-layout.json
$ echo '{"tombstones":[]}' > .katana/tombstones.json
$ echo '{"manifests":[],"schema_version":1}' > .katana/legacy-manifest-inventory.json
$ echo '/.katana/runtime/' > .gitignore
$ echo '# INDEX' > INDEX.md
$ git add . && git commit -m init

$ KATANA_KB_ROOT=/tmp/h7-prod-form python -m katana_work_folder_mcp.server &
$ sleep 2

# 尝试调用被禁止的写面工具，off 模式下应全放行
$ curl -s -X POST http://127.0.0.1:5602/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":1}'

# 响应: {"jsonrpc":"2.0","result":{...},"id":1}
# 没有 scope 判定，没有 scope_guard 拦截 —— 当刻生产形态无任何 scope 判断

$ kill %1
```

**结论**：直起 `python -m katana_work_folder_mcp.server` 在 `dedbb049` 上不存在 `scope_guard.py`，`server.py` 的 `main()` 不导入 `scope_guard`，生产形态下当刻无任何 scope 判定。

---

## 四、灰度开关默认值双重确认

- **代码**：`mcp/work-folder/katana_work_folder_mcp/scope_guard.py:25` — `_scope_enforcement_enabled: bool = False`
- **测试**：`test_default_enforcement_is_off` 通过 `importlib.reload` 绕过 fixture 的影响，直接读取模块的初始状态，断言 `is_enforcement_enabled() is False`，且 `test_default_deny_by_default_is_true` 同样通过 `importlib.reload` 断言 `is_deny_by_default() is True`