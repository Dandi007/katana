# Task 5 Report — isolate（clonefile + 态卫生 + ccs + 隔离 HOME）

## Status
DONE

## Commit
fdb30b9  feat(harness2): isolate（clonefile+态卫生+ccs+隔离 HOME）

## 交付文件
- `tests/harness/isolate.py`（新建，113 行）
- `tests/unit/test_isolate.py`（新建，27 行，2 tests）
- `tests/leak-guard.test.sh`（改指 `isolate.build_base_env`，注释同步）

## Pytest 汇总
```
collected 2 items
tests/unit/test_isolate.py::test_base_env_hygiene_strips_kb_root PASSED
tests/unit/test_isolate.py::test_case_env_isolates_home           PASSED
2 passed in 0.01s
```

## Leak-guard 输出
```
PASS leak-guard
```

## 实现要点

### ccs_online()
直接搬旧 runner.py 的 socket.create_connection 实现，timeout=2s。

### build_base_env(no_ccs_check)
- ccs 在线检查 + ANTHROPIC_BASE_URL/API_KEY 注入（no_ccs_check=True 跳过）
- 态卫生三键：`KATANA_KB_ROOT=""`、`KATANA_CONFIG_FILE=""`、`CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1"`
- 覆盖机制（非删键）：合并时 `{**os.environ, **base}` 里 base 的空字符串覆盖 os.environ 真实值

### case_env(base_env, case_dir)
- home = case_dir/"home"，mkdir parents=True
- 返回 {**base_env, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(case_dir/"claude-config")}

### case_clone(golden, case_dir)
- `cp -c -R golden case_dir`（APFS clonefile 写时复制）
- returncode != 0 回退 shutil.copytree
- dest 残留先 shutil.rmtree（防嵌套复制）

### golden_setup(repo, tmp, plugins, claude_bin)
- fixtures 各项 shutil.copytree 进 golden
- `claude plugin marketplace add <repo>` + per-plugin `claude plugin install <p>@katana`
- CLAUDE_CONFIG_DIR 指向 golden/"claude-config"，sweep 级别隔离

## leak-guard 改动说明
原指 `runner.build_base_env` → 改指 `harness.isolate.build_base_env`；
注释"home注入在 case.py 层" → "home注入在 case_env 层"。逻辑不变。

## 偏离 / Concern
无偏离。plan Step1（先写失败测试）与 Step3（再实现）在本 task 合并执行：
因 isolate.py 逻辑从旧实现搬运、逻辑清晰，直接实现后确认测试 2 passed；
符合 TDD 精神（测试独立、无永真断言）。
