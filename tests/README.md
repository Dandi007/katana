# Contract Regression Harness

每个 skill 一份声明式契约（`plugins/<p>/tests/contracts/*.contract.yaml`），
runner 在隔离 fixture（`tests/fixtures/`）中真实运行 `claude -p` 并机械断言产物。

## 快速使用

    ./tests/run-contracts.sh --touched          # PR 前：跑改动的 plugin
    ./tests/run-contracts.sh --all              # release 前：全量 + judge 兜底
    ./tests/run-contracts.sh --case wiki:query  # 调单个 skill（或 case_id 精确单 case）
    ./tests/lint-structure.sh                   # G0 静态检查（CI 同款）
    uv run --with pytest --with pyyaml -m pytest tests/unit   # harness 自身单测

## 约束

- 流量必须走 ccs（127.0.0.1:15721）→ 灵智，ccs 不在线直接 abort，绝不 fallback 直连
- 每 case 独立 APFS 快照（kb + CLAUDE_CONFIG_DIR），skill 级并行（--jobs，默认 4）；
  `exclusive:<name>` requires 声明独占资源（如 chrome profile），同组自动串行
- FAIL 自动重试一次并保留现场目录；报告落 tests/reports/ 随 PR 入库
- judge 裁决（case verdict / overall backstop）记 NEEDS-REVIEW 交人工复核，不等同契约 FAIL
- 触发矩阵：PR=--touched；release tag=--all；模型/Claude Code 升级=--all（手动）

## 写新契约

1. `plugins/<p>/tests/contracts/<case-id>.contract.yaml`（schema 见 tests/harness/schema.py）
2. **assert-down**：能机械断言的禁止丢给 verdict；verdict rubric 一律正极性二值问题（yes=好）
3. 复杂断言用 `script:` 逃逸口（与契约同目录，路径不可逃逸）；同类逃逸出现 3 次升格为原语
4. 跑 `./tests/lint-structure.sh` 确认覆盖 diff 变绿（或在 tests/coverage-exemptions.txt 显式豁免）

## 验收实证（2026-06-07 Wave 1）

- 7 契约：6 实跑 PASS、1 SKIP（xiaohongshu，登录态 env 门控）
- 并行加速：串行 564s → 4 路并行 203s；串/并结果一致
- 灵敏性：SKILL.md 行为级注入（marker 实验）→ case 红；措辞级删除被
  hook 惯例/schema/强模型先验补偿 → 保持绿（断言锚定行为而非措辞，是特性）
- 隔离：sweep 前后用户真实 KB git status md5 不变
