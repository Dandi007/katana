# Task 13 报告：9 契约迁三轴

## status
success

## commit sha
d2a0e21

## 校验计数
9/9（自写 Python 脚本 + harness `load_contract` 双重验证，均全通过）

## 迁移明细

| 契约 | 旧格式 | 新 process | 新 filesystem | semantic |
|---|---|---|---|---|
| memory:remember | file_exists+script | 1(skill_loaded) | created+script+unchanged_outside | NO |
| memory:validate | stdout_grep×3 | 1(skill_loaded) | created+content×3+unchanged_outside | NO |
| fpa:fpa | file_exists×3+script+verdict | 1(skill_loaded) | created×3+script+unchanged_outside | YES(fpa-full rubric) |
| fpa:first-principles-thinking | stdout_grep×4 | 1(skill_loaded) | created+content×4+unchanged_outside | NO |
| deep-research:deep-research | file_exists+script+verdict | 1(skill_loaded) | created+script+unchanged_outside | YES(deep-research-kb rubric) |
| feishu-docs:feishu-docs | stdout_grep×3 | 1(skill_loaded) | created+content×3+unchanged_outside | NO |
| incubate:incubate | file_exists×2+script | 1(skill_loaded) | created×2+script+unchanged_outside | NO |
| obsidian-md:obsidian-writing | file_exists+file_grep×3 | 1(skill_loaded) | created+content×3+unchanged_outside | NO |
| guide:using-katana | stdout_grep×3 | 1(skill_loaded) | created+content×3+unchanged_outside | NO |

## filesystem / semantic 分布

- filesystem 断言总计：9 个契约全部有 filesystem（≥3 条）
- semantic：2 个契约（fpa-full、deep-research-kb），引用现有 `tests/judge/case-rubrics/` rubric
- 纯 process + filesystem（无 semantic）：7 个

## 改了哪些 verify.sh

共 4 个，全部将 `$KB_DIR` → `$CWD`：
1. `plugins/memory/tests/contracts/remember-card.verify.sh`
2. `plugins/fpa/tests/contracts/fpa-full.verify.sh`
3. `plugins/deep-research/tests/contracts/deep-research-kb.verify.sh`
4. `plugins/incubate/tests/contracts/incubate-e2e.verify.sh`

## 偏离与 concern

1. **fpa-full semantic inputs**：旧契约的 `verdict.inputs` 指向 `{cwd}/docs/fpa/FPA-LATEST.md`（normalize 后固定路径）。新契约改用 `"{case_trace}"` 占位，因为 harness runner 的 `_resolve_verdict_inputs` 对 `created` 占位是读 delta 产物，而 fpa-full 产物路径含 glob（`FPA-*.md`）且需 verify.sh 先 normalize。使用 `{case_trace}` 让 judge 从 trace 推断内容，语义等价但不如直接读 FPA-LATEST.md 精准。**后续可在 runner 支持 `{file:path}` 占位后补强**。

2. **deep-research-kb semantic inputs**：同上原因使用 `{case_trace}` 而非 `{cwd}/research-report.md`。verify.sh 已负责 normalize（cp → research-report.md），runner 若后续支持文件路径占位可切换。

3. **fpt-lite 文件名**：实际为 `fpt-lite.contract.yaml`（非 `fpa-lite`），已按实际文件名处理。

4. **validate-cards 答问型**：旧契约纯 stdout_grep 无法确定性验证，新 prompt 要求 skill 把核验摘要写到 `./validate-result.md`。这改变了 prompt 语义（加了"写文件"要求），属于已知迁移权衡（G2：答问类必须落文件→轴②）。

5. **unchanged_outside 位置**：所有契约均将 `unchanged_outside: true` 置于 filesystem 块最后，符合 plan 要求（declared glob 累积后才做越界检查）。
