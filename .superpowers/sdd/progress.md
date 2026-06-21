# SDD Progress — katana E2E harness v2

worktree `/Volumes/Data/code/worktrees/katana/e2e-v2`(feat/e2e-harness-v2 off main 383eabc)。
plan `智元工作/工作记录/2026/06/21/katana-e2e-harness-v2/plan.md`；golden-order G0–G7。

## Task ledger
- [x] Task 1: 三轴 schema + 不变量 ✅ ed589ff（4 passed；stdout_grep=0、不变量逻辑核对）
- [x] Task 2: model.py + models.yaml + gpt 修 ✅ f323b8c（2+8 passed；setter=env 管道、roster 显式 model）
- [x] Task 3: snapshot.py delta 核心 ✅ 723cfc5（2 passed，created/modified/deleted+排除）
- [x] Task 4: trigger.py + trace.py ✅ 6a1b62e（14 passed；单/多轮/超时/trace；trace.py 已对真实 schema 未改）
- [x] Task 5: isolate.py 态卫生 ✅ fdb30b9（2 passed+leak-guard PASS；KATANA_KB_ROOT 覆盖空、betas-disable 纳入、HOME 隔离）
- [x] Task 6: expect_process.py 轴① ✅ ee8dd35（6 passed 含负向；读 input.skill）
- [x] Task 7: expect_fs.py 轴②delta ✅ 5ebdfd6（2 passed；inline 冷读：unchanged_outside declared 累积正确、content/script 逻辑 sound）
- [x] Task 8: judge.py 可插拔 ✅ 55e74bf（6 passed；SingleJudge 改走 trigger.run + JuryJudge stub）。**Minor 滚存**：旧 run_case_verdict 向后兼容残留→Task15 清。
- [x] Task 9: runner.py 六步 ✅ b370d3c+dbc0b4c+fix 0730d95（84 passed）。**冷读抓 2 Critical**：C1 retry 误用到轴断言 FAIL(掩盖回归,测试还固化 attempts==2)→只 flake retry；C2 未知 requires 静默忽略(假 PASS)→raise。+I1/I2/m3(unchanged_outside 顺序无关)。
- **harness 核心 Task 1–9 完成+评审**。
- [x] Task 10: 试点 3 契约 + 真机验三轴 ✅ fda24ff+fix 53f2d1f。**真机全 PASS**（wiki:ingest delta/search-note 落文件/checkpoint 多轮）。**试点抓到 harness bug**：多轮 trace 只留末轮→skill_loaded 假阴性(也会 tool_absent 假阳性)→修成累积全轮事件。契约补合法写声明(ingest backlink、checkpoint context/findings)。**三轴 harness 真机验证通过**。
  - 迁移样板实证：search-note 旧 `stdout_grep:温度A/B`→让 skill 写 search-result.md→filesystem.content 确定性断言，真机 PASS。
  - pilot-run.py 在 .superpowers/sdd/（scratch，绕 discover 跑单契约用）。
- [x] Task 11（见下方进度）
- [ ] Task 12: wiki 剩 6 + writing 7
- [x] Task 13: 7 plugin 9 契约迁 ✅ d2a0e21（9/9 valid；2 semantic fpa/deep-research，4 verify.sh 改 $CWD）
- [x] Task 14: work-folder resume + jury review-smoke 迁 ✅ 6c6e2d1（3/3；jury 白盒→process+fs+script，verify.sh $CWD）。**40 契约全迁完**。
- [ ] Task 15: 闸门(stdout_grep=0) + 原子退役旧 case/claude_cli/asserts
- [ ] Task 16: 终审 + jury 自举 + 开 PR

## Minor findings 滚存（交终审）
- Task 8: 旧 run_case_verdict 向后兼容残留 → Task15 清。
- **script env 迁移规则（Task 12-15 必守）**：新 expect_fs script 注入 `CWD`/`DELTA_JSON`（非旧 `KB_DIR`/`CASE_LOG`/`CASE_DIR`）。所有迁移的 `.verify.sh` 引用 `$KB_DIR`→`$CWD`。xiaohongshu-download.verify.sh 待修。Task15 grep `KB_DIR` 全仓应 0（除注释）。

## 进度
- [x] Task 11: retrieval 13 迁 ✅ 36a3e55（14 valid；10 转 filesystem-content、2 转 semantic、1 直迁）
- [x] Task 12: wiki 6 + writing 7 迁 ✅ b3a0166（14 OK）。**Task15 sweep 须验的 live 隐患**：①query-cold/ironrules prompt 加"写 answer.md"需 skill 配合 ②writing `tools:[]` 空——确认能否 skill_loaded（DEFAULT_TOOLS 无 Skill 但 pilot 经验 slash 命令仍加载）③lint-summary-backfill `{created}` 占位但实为 modified→semantic input 可能找不到路径(改 {case_trace})。

## Changelog
| 时间 | 事件 |
|---|---|
| 17:2x | worktree 建；ccs UP；开跑 Task 1 |
