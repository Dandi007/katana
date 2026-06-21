# Task 2 报告：模型配置干净化 + gpt 通用修

**status:** DONE
**commit:** f323b8c
**branch:** feat/e2e-harness-v2

## pytest 汇总

```
tests/unit/test_model.py        2 passed in 0.04s
plugins/jury/engine/test_panel.py  8 passed in 1.44s
```

## 落地内容

| 文件 | 动作 | 说明 |
|---|---|---|
| `tests/models.yaml` | 新建 | roles + jury-roster，全显式 model；gpt=gpt/gpt-5.5 |
| `tests/harness/model.py` | 新建 | `load_models`/`build_env`（回收 ANTHROPIC_*/CLAUDE_CODE_* env）/`role` |
| `tests/unit/test_model.py` | 新建 | 2 个单元测试（build_env 收 env；roster gpt model 显式） |
| `plugins/jury/engine/panel.py` | 改 DEFAULT_ROSTER | gpt `""`→`"gpt/gpt-5.5"`；deepseek→`"lingzhi/deepseek-v4-pro"`；qwen→`"lingzhi/qwen3.7-max"`；opus 保持 `"opus"` |

## G5 约束满足

- `build_env` 仅回收 setter 产生的 `ANTHROPIC_*`/`CLAUDE_CODE_*` env，不做任何 model 决策。
- panel.py `run_model` 内 `model_arg` 逻辑不变（`member.get("model")` 非空才传 `--model`）；现在 roster 四项全非空，gpt 从此显式传 `gpt/gpt-5.5` 而非裸继承 setter 环境中的 5.4 主槽。

## concern / 偏离

无。test_panel.py 存量测试 `test_run_model_records_ccs_base_url` 用了 `"model": ""` 构造非显式场景，属测试内部 fixture 构造，不影响 DEFAULT_ROSTER 语义，保留原样（plan Step5 说"model 非空只是多传 --model，不破坏现有测试"——确实全绿）。
