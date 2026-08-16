# H7 Scope Narrowing — Evidence

## 先红后绿判据

| # | 用例 | 基底结果 | 当前结果 |
|:--:|---|---|---|
| 1 | 允许集合内工具放行 | 绿（当刻无闸） | 绿 |
| 2 | 禁止集合内工具拒绝 | 必红 | 绿 |
| 3 | deny-by-default 拒绝 | 必红 | 绿 |
| 4 | 灰度开关 off → 全放行但写审计 | 必红 | 绿 |
| 5 | 灰度开关 on → R1 拦截 | 必红 | 绿 |
| 6 | 必需工具 on 时仍放行 | 必红 | 绿 |
| 7 | wf_save on 时仍放行 | 必红 | 绿 |
| 8 | 变异：allow-by-default → 用例 3 转红 | 变异证据 | 绿 |
| 9 | 变异：空允许集合 → 用例 1/6/7 转红 | 变异证据 | 绿 |

## 生产形态下当刻无任何 scope 判定

基线 commit `557e2ed89564410a1ce8c8cc7994668ff8ca5853` 上，`mcp/work-folder/katana_work_folder_mcp/` 目录内不存在 `scope_guard.py` 模块，
且 `server.py` 的工具函数体内无任何 scope 判定逻辑。该闸为本次新增，基底无该能力。

## 灰度开关默认值

`scope_guard.py` 中 `_scope_enforcement_enabled` 初始值为 `False`（off）。
测试 `test_default_enforcement_is_off` 双重确认。

## 测试结果

```
1426 passed, 1 warning in 87.52s
```

具名失败集差集为空（0 → 0）。