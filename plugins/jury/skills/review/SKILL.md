---
name: review
description: 用多个不同大模型并行 review 代码改动（支持本地 worktree 路径 / PR 链接 / 当前分支），有 spec 时自动切换 spec 符合性 rubric，保留各模型分歧并给出投票裁决。
---

# jury:review — 多模型并行评审（v0.2）

对指定目标的代码改动做**多模型交叉评审**：同一份评审 rubric 并行打给 N 个不同模型（默认 Opus 原生 + GPT-5.5 + DeepSeek-V4-Pro + Qwen），保留各模型分歧，给出投票裁决。**不合并、不调和分歧**——分歧本身是信号。

## 入参

| 参数 | 含义 | 缺省 |
|------|------|------|
| `--target <worktree路径\|PR链接>` | 评审目标：本地 worktree 绝对路径 或 GitHub PR 链接 | 当前分支 vs base diff |
| `--spec <file>` | spec 文件路径（相对或绝对），切换 spec 符合性 rubric | 无（用通用质量 rubric） |
| `--prompt <额外说明>` | 附加给评审员的补充说明文字 | 无 |

## 执行流程

### 1. 解析 target → diff + TARGET_DIR

```
if --target 是本地路径（以 / 开头或以 ./ 开头，且路径存在）:
    TARGET_DIR="<path>"
    DIFF=$(git -C "$TARGET_DIR" diff $(git -C "$TARGET_DIR" merge-base main HEAD)...HEAD)
elif --target 是 PR 链接（包含 github.com/*/pull/）:
    TARGET_DIR=""          # 远程 PR，无本地 worktree cwd
    DIFF=$(gh pr diff <PR_URL>)
    # 注：PR 链接路径后续可深化（克隆仓库、提取 target_dir）
else（缺省，无 --target）:
    TARGET_DIR=$(git rev-parse --show-toplevel)
    BASE=$(git merge-base main HEAD 2>/dev/null || git merge-base origin/HEAD HEAD)
    DIFF=$(git diff "${BASE}...HEAD")
fi
```

空 diff 则告知用户无改动、停止。

### 2. 选模板

```
if [ -n "$SPEC_FILE" ]; then
    RUBRIC="${CLAUDE_SKILL_DIR}/../../templates/review-rubric-spec.md"
else
    RUBRIC="${CLAUDE_SKILL_DIR}/../../templates/review-rubric.md"
fi
```

### 3. 组 prompt 文件

```
JURY_DIR="${TARGET_DIR:-$(git rev-parse --show-toplevel)}/.jury"
mkdir -p "$JURY_DIR"
PROMPT_FILE="$JURY_DIR/prompt.md"

# 写 prompt：rubric 模板 + diff 块 + 可选附加说明
{
    cat "$RUBRIC"
    echo ""
    echo '```diff'
    echo "$DIFF"
    echo '```'
    if [ -n "${EXTRA_PROMPT:-}" ]; then
        echo ""
        echo "## 附加说明"
        echo "$EXTRA_PROMPT"
    fi
} > "$PROMPT_FILE"
```

spec 内容**不塞进 prompt 文件**，而是通过引擎 `--spec-file` 参数传入（引擎会在 prompt 前置 spec 块）。

### 4. 调引擎

```bash
"${CLAUDE_SKILL_DIR}/../../engine/panel.py" \
    --prompt-file "$PROMPT_FILE" \
    --out "$JURY_DIR" \
    ${TARGET_DIR:+--target-dir "$TARGET_DIR"} \
    ${SPEC_FILE:+--spec-file "$SPEC_FILE"}
```

引擎并行扇出、落三产物到 `$JURY_DIR/`：`panel-meta.json`、`jury-verdict.json`、`jury-report.md`，各模型 trace 在 `$JURY_DIR/<name>.trace.jsonl`。

### 5. 汇报 verdict

读 `$JURY_DIR/jury-verdict.json`，向用户呈现：

- 每项多数决结论（yes/no）
- dissent：哪些项模型间有分歧（分歧本身是信号，不调和）
- 指向 `jury-report.md` 看各模型原文及 evidence

**你的角色是编排 + 如实转述**，不替模型下结论，不把自己的判断混入投票。如实呈现分歧，分歧项明确列出哪些模型持异见。

## 约束

- **评审员只读**：引擎限 `allowedTools Read,Grep,Glob`，无 Write/Edit/Bash（G9 守则）。
- `.jury/` 目录加进 `.gitignore`（运行时产物，不入库）。已确认 gitignore 已覆盖。
- 引擎需要 ccs（灵智三路）在线 + 本机 Claude 订阅（Opus 原生路）。某路挂 → 引擎记 partial quorum，照常输出剩余模型结果。
- 模板路径：`${CLAUDE_SKILL_DIR}/../../templates/review-rubric.md`（通用）、`${CLAUDE_SKILL_DIR}/../../templates/review-rubric-spec.md`（spec 符合性）。
- 引擎路径：`${CLAUDE_SKILL_DIR}/../../engine/panel.py`。
