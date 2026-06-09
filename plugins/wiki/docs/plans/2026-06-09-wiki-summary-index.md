# wiki 摘要索引层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 wiki 每页 frontmatter 带一行 schema 声明的 `摘要` 自描述字段，由 ingest 在写页时生成、由 lint 检测并批量 backfill 存量，使 query/explore 的候选预览只读 frontmatter 即可。

**Architecture:** 零新 skill。改动落在三个现有件：①ingest 写新页时按 §3 生成 `摘要`；②lint 把"摘要 backfill"加进它的写入白名单并加批量治理（抽检 + 自动批量）；③init 模板把 `摘要` 作为新库推荐约定。schema 字段本身在每个库的 `WIKI.md`（plugin 不硬编码字段名，只认"schema 声明的 summary 字段"）。测试走本仓库既有的 contract 机制（live `claude` + 确定性 `script` 断言）+ G0 结构 lint。

**Tech Stack:** Markdown skill 指令（plugin 行为即 prose）、YAML contract、bash 断言脚本、Python contract runner（`uv run tests/runner.py`）、G0 (`tests/lint-structure.sh`)。

> **两部分：** Part A（Task 1–5）= katana plugin 能力，在本 worktree（branch `feat/wiki-summary-index`）完成并开 PR。Part B（Task 6–7）= 对真实 Zettelkasten 库的 rollout，**PR 合并后**在 Zettelkasten repo 执行，不在本 PR 内。

---

## File Structure

**katana plugin（本 worktree）**

- Modify: `plugins/wiki/skills/lint/SKILL.md` — §1 检测项点名 summary 字段；§4 写入白名单加 summary backfill + 批量治理段。
- Modify: `plugins/wiki/skills/ingest/SKILL.md` — §5 新页 draft 显式含一行 summary。
- Modify: `plugins/wiki/skills/init/templates/schema.md` — 把 summary 字段写进推荐页面约定。
- Create: `plugins/wiki/tests/fixtures/kb-summary/` — 专用小 fixture 库（独立于共享 `kb/`，避免动到其它 contract 的计数断言）。
- Create: `plugins/wiki/tests/contracts/lint-summary-backfill.contract.yaml` — 新 contract。
- Create: `plugins/wiki/tests/contracts/scripts/check-summary-backfill.sh` — 确定性断言脚本。
- Create: `plugins/wiki/tests/case-rubrics/wiki-lint-summary.md` — judge rubric。

**Zettelkasten 库（Part B，合并后）**

- Modify: `WIKI.md` — §3 声明 `摘要`，§5 注明 ingest 生成、§7 注明 lint 可 backfill。
- Modify: `Zettelkasten/*.md`（592 页）— frontmatter 各加一行 `摘要`（lint backfill 产出）。

---

## Task 1: 专用 fixture 库 + 确定性断言脚本（先写测试）

先建一个小 fixture：schema 声明 `摘要` 必填，三张笔记里两张缺 `摘要`、一张已有。断言脚本检查 lint 跑完后缺的两张补上了 `摘要`、且正文 byte 不变。

**Files:**
- Create: `plugins/wiki/tests/fixtures/kb-summary/WIKI.md`
- Create: `plugins/wiki/tests/fixtures/kb-summary/.katana`
- Create: `plugins/wiki/tests/fixtures/kb-summary/笔记/手冲咖啡萃取.md`
- Create: `plugins/wiki/tests/fixtures/kb-summary/笔记/咖啡豆烘焙度.md`
- Create: `plugins/wiki/tests/fixtures/kb-summary/笔记/V60滤杯.md`
- Create: `plugins/wiki/tests/contracts/scripts/check-summary-backfill.sh`
- Create: `plugins/wiki/tests/fixtures/kb-summary/.golden/手冲咖啡萃取.body`
- Create: `plugins/wiki/tests/fixtures/kb-summary/.golden/咖啡豆烘焙度.body`
- Create: `plugins/wiki/tests/fixtures/kb-summary/.golden/V60滤杯.body`

- [ ] **Step 1: 确认 fixture 解析约定**

Read: `plugins/wiki/tests/harness/case.py` — 确认 contract 的 `input.cwd: <name>` 如何映射到 `tests/fixtures/<name>`，以及 runner 是否把 fixture 复制到临时 cwd 后再跑（lint 会写文件，必须跑在副本上）。把约定记在脑里，后面 contract 的 `cwd` 字段照此填。

- [ ] **Step 2: 写 fixture schema**

`plugins/wiki/tests/fixtures/kb-summary/WIKI.md`：

```markdown
# WIKI Schema (summary-backfill fixture)

## 2. Zones

| Zone | Path | Purpose | Write policy | Page template | Naming |
|------|------|---------|--------------|---------------|--------|
| 笔记 | `笔记/` | thinking | propose | 原子卡片 | 中文概念名，允许空格 |

## 3. Page Conventions

- **必填 frontmatter：**
  - `摘要`：一行一句话（≤~40 字），描述本页讲什么 + 核心定义/结论，只描述页面自身。new requirement，存量由 lint backfill。

## 7. Lint Rules

- **摘要 backfill：** 缺 `摘要` 的页为可修复 finding；lint 读全页生成一行并写入 frontmatter（不碰正文）。批量写走抽检 + 自动批量（见 lint skill §4）。raw / inbox 豁免。
```

`plugins/wiki/tests/fixtures/kb-summary/.katana`：

```
wiki_root=.
```

- [ ] **Step 3: 写三张笔记（两张缺摘要，一张已有）**

`笔记/手冲咖啡萃取.md`（缺摘要）：

```markdown
---
创建日期: 2025-01-01 10:00
tags:
  - 咖啡
---
# 手冲咖啡萃取
手冲咖啡推荐水温 90–94°C，粉水比 1:15。烘焙度越深，水温应越低，见 [[咖啡豆烘焙度]]。器具选择见 [[V60滤杯]]。
# References
- 个人冲煮笔记
```

`笔记/咖啡豆烘焙度.md`（缺摘要）：

```markdown
---
创建日期: 2025-01-01 10:00
tags:
  - 咖啡
---
# 咖啡豆烘焙度
烘焙度从浅到深影响酸度与醇厚度；越深越苦、酸度越低，萃取水温应相应下调，见 [[手冲咖啡萃取]]。
# References
- 个人冲煮笔记
```

`笔记/V60滤杯.md`（已有摘要，验证 lint 不重复加）：

```markdown
---
创建日期: 2025-01-01 10:00
摘要: 锥形单孔滤杯，靠注水节奏控制萃取速率
tags:
  - 咖啡
---
# V60滤杯
V60 是锥形单孔滤杯，萃取速率由注水节奏决定，灵活但对手法敏感，见 [[手冲咖啡萃取]]。
# References
- 个人冲煮笔记
```

- [ ] **Step 4: 生成 golden 正文快照**

正文 = 第二个 `---` 之后到 EOF。用 awk 抽出，存进 `.golden/`：

Run:
```bash
cd plugins/wiki/tests/fixtures/kb-summary
mkdir -p .golden
for f in 手冲咖啡萃取 咖啡豆烘焙度 V60滤杯; do
  awk 'c>=2{print} /^---$/{c++}' "笔记/$f.md" > ".golden/$f.body"
done
wc -l .golden/*.body
```
Expected: 三个 `.body` 文件，各非空（H1 + 正文 + References）。

- [ ] **Step 5: 写确定性断言脚本**

`plugins/wiki/tests/contracts/scripts/check-summary-backfill.sh`：

```bash
#!/usr/bin/env bash
# 断言：lint 跑完后，笔记/ 下每页 frontmatter 都有非空 摘要，且正文 byte 不变。
# 由 contract 的 script 断言调用，env: KB_DIR（lint 跑过的库副本）。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOLDEN="$KB_DIR/.golden"
fail=0
for f in 手冲咖啡萃取 咖啡豆烘焙度 V60滤杯; do
  page="$KB_DIR/笔记/$f.md"
  [ -f "$page" ] || { echo "MISSING: $page"; fail=1; continue; }
  # 1) frontmatter 内有非空 摘要（只看第一个 frontmatter 块）
  summ="$(awk 'c==1 && /^摘要:[[:space:]]*[^[:space:]]/{print; exit} /^---$/{c++}' "$page")"
  [ -n "$summ" ] || { echo "NO-SUMMARY: $f"; fail=1; }
  # 2) 正文（第二个 --- 之后）与 golden 一致
  body="$(awk 'c>=2{print} /^---$/{c++}' "$page")"
  if ! diff -q <(printf '%s' "$body") "$GOLDEN/$f.body" >/dev/null 2>&1; then
    echo "BODY-CHANGED: $f"; fail=1
  fi
done
exit $fail
```

注：golden 随 fixture 一起被 runner 复制进副本，故用 `$KB_DIR/.golden`。`chmod +x` 该脚本。

- [ ] **Step 6: 对脚本做 fail-first 单测（手造 before/after 副本，不跑 claude）**

Run（手造一个"已 backfill"的副本验证脚本通过，再造一个"未 backfill"的验证脚本失败）：
```bash
cd plugins/wiki/tests
# A) 未 backfill 的库副本 → 脚本应失败（两张缺摘要）
rm -rf /tmp/kbA && cp -R fixtures/kb-summary /tmp/kbA
KB_DIR=/tmp/kbA bash contracts/scripts/check-summary-backfill.sh; echo "exit=$? (expect non-zero: NO-SUMMARY x2)"
# B) 手动给两张补上摘要 → 脚本应通过
rm -rf /tmp/kbB && cp -R fixtures/kb-summary /tmp/kbB
sed -i '' '2a\
摘要: 手冲萃取的水温与粉水比要点
' /tmp/kbB/笔记/手冲咖啡萃取.md
sed -i '' '2a\
摘要: 烘焙度对酸苦与萃取水温的影响
' /tmp/kbB/笔记/咖啡豆烘焙度.md
KB_DIR=/tmp/kbB bash contracts/scripts/check-summary-backfill.sh; echo "exit=$? (expect 0)"
```
Expected: A 非零并打印两条 `NO-SUMMARY`；B 退出 0。若 B 报 `BODY-CHANGED`，说明 awk 正文切分与 golden 不一致 —— 修脚本/golden 直到 B=0。

- [ ] **Step 7: Commit**

```bash
git add plugins/wiki/tests/fixtures/kb-summary plugins/wiki/tests/contracts/scripts/check-summary-backfill.sh
git commit -m "test(wiki): summary-backfill fixture + deterministic assert script"
```

---

## Task 2: 新 contract + judge rubric（接上确定性断言）

把断言脚本挂进一个 live contract：让 `/wiki:lint` 对 `kb-summary` 跑 backfill，再用 `script` 断言 + judge rubric 验收。此刻 contract 会因为 lint skill 还不会 backfill 摘要而**失败**（fail-first）。

**Files:**
- Create: `plugins/wiki/tests/contracts/lint-summary-backfill.contract.yaml`
- Create: `plugins/wiki/tests/case-rubrics/wiki-lint-summary.md`

- [ ] **Step 1: 写 contract**

`plugins/wiki/tests/contracts/lint-summary-backfill.contract.yaml`（`cwd` 按 Task 1 Step 1 确认的约定填；若 fixture 名即 cwd 值则为 `kb-summary`）：

```yaml
skill: wiki:lint
input:
  prompt: "用 /wiki:lint 对本库做一次摘要 backfill：给所有缺 摘要 frontmatter 的页补上一行 摘要，不要改正文。本次提案视为已批准，直接写入。"
  cwd: kb-summary
assert:
  - script: scripts/check-summary-backfill.sh
verdict:
  rubric: case-rubrics/wiki-lint-summary.md
  inputs: ["{cwd}/笔记/手冲咖啡萃取.md", "{cwd}/笔记/咖啡豆烘焙度.md"]
```

- [ ] **Step 2: 写 judge rubric**

`plugins/wiki/tests/case-rubrics/wiki-lint-summary.md`：

```markdown
# Rubric: wiki:lint 摘要 backfill

判定补出的 `摘要` 是否合格。PASS 需全部满足：

1. 两张原本缺摘要的页（手冲咖啡萃取、咖啡豆烘焙度）frontmatter 现各有一行非空 `摘要`。
2. 摘要是**一句话**、贴合该页主旨（讲什么 + 核心结论），中文为主、术语可保留英文，无明显跑题/复制整段正文。
3. 正文未被改写（断言脚本已机械保证；此处只看摘要质量）。

任一不满足 → FAIL，指出具体页与问题。
```

- [ ] **Step 3: G0 结构校验（contract schema 合法 + 覆盖）**

Run:
```bash
cd /Volumes/Data/code/worktrees/katana/wiki-summary-index
bash tests/lint-structure.sh
```
Expected: 通过（新 contract schema 合法；`wiki:lint` 已被既有 lint-full contract 覆盖，故不会触发"no contract"）。若报 schema 错，按报错修 contract YAML 字段。

- [ ] **Step 4: 跑新 contract（fail-first，证明当前 lint 不会 backfill）**

Run（需 `uv` + `claude` CLI；只跑这一个 case）:
```bash
cd /Volumes/Data/code/worktrees/katana/wiki-summary-index
bash tests/run-contracts.sh --only lint-summary-backfill 2>&1 | tail -30
```
（`--only` 的确切 flag 以 `bash tests/run-contracts.sh --help` 为准；不支持则跑全量后看该 case。）
Expected: **FAIL** — `script` 断言打印 `NO-SUMMARY`，因为 lint skill 尚未学会 backfill 摘要。这正是 fail-first 的预期。

- [ ] **Step 5: Commit**

```bash
git add plugins/wiki/tests/contracts/lint-summary-backfill.contract.yaml plugins/wiki/tests/case-rubrics/wiki-lint-summary.md
git commit -m "test(wiki): live contract + rubric for lint summary backfill (fail-first)"
```

---

## Task 3: lint skill 学会 backfill 摘要

给 lint 两处改动：§1 把 summary 字段点进缺失检测；§4 把 summary backfill 加进写入白名单，并加"抽检 + 自动批量"治理段（这是唯一突破 lint "只报不写" 的地方，严格限定在 schema 声明的 summary 字段）。

**Files:**
- Modify: `plugins/wiki/skills/lint/SKILL.md`

- [ ] **Step 1: §1 检测项点名 summary 字段**

在 `## 1. Mechanical checks` 的 "**Missing required frontmatter**" 项（当前 SKILL.md:64-66）末尾追加一句：

```
  当 §3 声明了 per-page **summary 字段**（一行自描述摘要，如本库的 `摘要`）时，
  缺它的页同样计入本检查 —— 它是 backfill-class finding（修复见 §4），不是单纯报告项。
```

- [ ] **Step 2: §4 写入白名单加 summary backfill + 批量治理**

把 `## 4. Fix proposals` 里 "**Lint may apply only:**" 那段（当前 SKILL.md:116-118）替换为：

```
**Lint may apply only:** conflict/stale annotations, broken-link fixes, index
entry back-fills, **and summary-field backfill** (filling a missing
schema-declared per-page summary line). Anything else — building a page, merging
pages, rewriting content — is **handed to `/wiki:ingest`** or listed as a human
to-do. Never do it here.

### Summary-field backfill（schema-declared summary 字段专属）

A schema-declared per-page **summary** field (one line, self-describing, e.g.
`摘要`) is **derived-from-self metadata** — a compression of content already on
the page, not new knowledge. The model-collapse defense (§4 raw-immutability /
no-re-ingesting-synthesis) does not apply, and the field never touches the body.
So lint MAY generate and write it, governed as follows instead of per-fix propose:

1. **Generate from the page itself** — read the full page, write one line (≤~40
   chars per schema §3): what the page is about + its core definition/claim.
   Never invent beyond the page; never copy a whole paragraph.
2. **Insert into frontmatter only** — add the `摘要:` line to the page's
   frontmatter block. **Never edit the body** (assert this: body bytes unchanged).
   Skip pages that already have a non-empty summary.
3. **Batch governance — sampling QC, then autonomous batch:** when backfilling
   many pages (e.g. a first full-library run), generate a **random sample of N=10**
   first and show them for human QC of quality. On approval, **write all remaining
   pages autonomously — do NOT AskUserQuestion per page** (that does not scale to
   hundreds). A wrong summary is cheap to regenerate (rerun lint). In
   non-interactive mode, only run the batch if the prompt pre-authorizes it.
4. **Scale via Workflow when large:** for a big backfill, fan out summarizers
   (one agent reads one page → returns its summary line); apply the returned lines.
   raw / inbox zones are exempt.
```

- [ ] **Step 3: 跑断言与 contract，转绿**

Run:
```bash
cd /Volumes/Data/code/worktrees/katana/wiki-summary-index
bash tests/lint-structure.sh
bash tests/run-contracts.sh --only lint-summary-backfill 2>&1 | tail -30
```
Expected: G0 通过；contract 现 **PASS** —— `script` 断言不再报 `NO-SUMMARY`，两张页 frontmatter 各有非空 `摘要`、正文 byte 不变；judge PASS（摘要一句话且贴题）。
若 `BODY-CHANGED`：lint 改了正文 —— 检查 §4 第 2 条措辞是否够硬。若 judge FAIL：摘要跑题/过长 —— 收紧 §4 第 1 条的长度与"从本页生成"约束。

- [ ] **Step 4: Commit**

```bash
git add plugins/wiki/skills/lint/SKILL.md
git commit -m "feat(wiki): lint backfills schema-declared per-page summary (sampling QC + autonomous batch)"
```

---

## Task 4: ingest 写新页时生成摘要

ingest §5 已要求新页带"required frontmatter (§3)"。补一句显式点名 summary 字段，确保新页 100% 覆盖，源头不欠债。

**Files:**
- Modify: `plugins/wiki/skills/ingest/SKILL.md`

- [ ] **Step 1: §5 New-page drafts 点名 summary**

在 `## 5. Build the proposal package` 的 "**New-page drafts**" 项（当前 SKILL.md:77-79）末尾追加：

```
  当 §3 声明了 per-page summary 字段（如 `摘要`），draft 的 frontmatter
  必须含一行该摘要：一句话、≤~40 字、描述本页讲什么 + 核心结论，从本页内容生成。
```

- [ ] **Step 2: 跑 G0 + 既有 ingest contract 不回归**

Run:
```bash
cd /Volumes/Data/code/worktrees/katana/wiki-summary-index
bash tests/lint-structure.sh
ls plugins/wiki/tests/contracts/ | grep ingest
bash tests/run-contracts.sh --only ingest-inbox 2>&1 | tail -20
```
Expected: G0 通过；既有 `ingest-inbox` contract 仍 PASS（fixture `kb` 的 WIKI.md 未声明 摘要，故 ingest 不被要求加，行为不回归）。

- [ ] **Step 3: Commit**

```bash
git add plugins/wiki/skills/ingest/SKILL.md
git commit -m "feat(wiki): ingest emits schema-declared per-page summary on new pages"
```

---

## Task 5: init 模板把 summary 作为新库推荐约定 + 全量回归 + PR

让 `/wiki:init` 新建/adopt 的库默认带上 summary 约定；跑全量 contract，开 PR。

**Files:**
- Modify: `plugins/wiki/skills/init/templates/schema.md`

- [ ] **Step 1: 读模板，定位 Page Conventions 段**

Read: `plugins/wiki/skills/init/templates/schema.md` — 找到页面约定 / frontmatter 段。

- [ ] **Step 2: 加 summary 推荐字段**

在该模板的页面约定段加入一条（措辞贴合模板既有风格）：

```
- **`摘要`（推荐）：** 每页 frontmatter 一行自描述摘要（一句话，≤~40 字：这页讲什么 +
  核心结论）。供检索/关联走读只读 frontmatter 即可预览页面，不必翻正文。
  ingest 新建页生成、lint 可对存量 backfill。
```

- [ ] **Step 3: 全量 G0 + 全量 contract**

Run:
```bash
cd /Volumes/Data/code/worktrees/katana/wiki-summary-index
bash tests/lint-structure.sh
bash tests/run-contracts.sh 2>&1 | tail -40
```
Expected: G0 通过；全部 wiki contract（含新 `lint-summary-backfill` 与既有 init/ingest/lint/query）PASS，无回归。

- [ ] **Step 4: Commit + push + PR**

```bash
git add plugins/wiki/skills/init/templates/schema.md
git commit -m "feat(wiki): init template recommends per-page 摘要 convention"
git push -u origin feat/wiki-summary-index
gh pr create --repo Dandi007/katana --base main \
  --title "feat(wiki): per-page 摘要 index layer (ingest emits, lint backfills)" \
  --body "见 plugins/wiki/docs/specs/2026-06-09-wiki-summary-index.md。Phase 1：摘要索引层。Phase 2（/wiki:explore 关联走读）后续单独 PR。"
```

---

## Task 6（Part B / rollout，PR 合并后在 Zettelkasten repo）：声明 live schema 字段

切到 Zettelkasten 库，在真实 `WIKI.md` 声明 `摘要`。

**Files:**
- Modify: `WIKI.md`（Zettelkasten repo 根）— §3 / §5 / §7

- [ ] **Step 1: §3 加必填字段**

在 §3 Page Conventions 的"必填 frontmatter"列表后加：

```
- **`摘要`（new requirement，仅 ingest/lint 维护的页适用，存量靠 lint backfill）：**
  YAML 标量，一行一句话（≤~40 字）：这页讲什么 + 核心定义/结论，只描述页面自身。
  对原子卡 ≈ "一句话定义"的浓缩；对 Index/MOC ≈ 这篇聚合了什么。
```

- [ ] **Step 2: §5 / §7 各加一句**

§5 Ingest Specifics 末尾：`- ingest 新建/更新页必生成 `摘要`（§3）。`
§7 Lint Rules 末尾：`- **摘要 backfill：** 缺 `摘要` 的页为可修复 finding，lint 读全页生成一行写入 frontmatter（不碰正文），批量走抽检 + 自动批量；raw/inbox 豁免。`

- [ ] **Step 3: Commit（Zettelkasten repo，按本仓库 PR 规范）**

按 Zettelkasten 仓库规范走 `/git`（feedback：代码改动走 PR）。提交信息：`docs(wiki): schema 声明 per-page 摘要 字段`。

---

## Task 7（Part B / rollout）：592 页 bootstrap backfill

对真实库跑首次全量摘要 backfill。

- [ ] **Step 1: 统计缺摘要页数（基线）**

Run（在 Zettelkasten repo）:
```bash
cd "$(git rev-parse --show-toplevel)"
find Zettelkasten -maxdepth 1 -type f -name '*.md' | wc -l
find Zettelkasten -maxdepth 1 -type f -name '*.md' -exec grep -L '^摘要:' {} + | wc -l
```
Expected: ~592 总数；缺摘要约等于总数（当前 0 页有摘要）。记下数字作 backfill 后对比。

- [ ] **Step 2: 抽检样本（N=10）**

用 `/wiki:lint` 对 `Zettelkasten/` 跑摘要 backfill 的**抽检模式**：随机 10 页生成摘要、展示给青林过目质量。不通过则调整生成约束重抽。

- [ ] **Step 3: 自动批量写（Workflow fan-out）**

抽检通过后，对剩余缺摘要页 fan-out 并行 summarizer（每 agent 读一页 → 返回一行摘要 → 写入 frontmatter，不碰正文）。完成后复检：

Run:
```bash
find Zettelkasten -maxdepth 1 -type f -name '*.md' -exec grep -L '^摘要:' {} + | wc -l
```
Expected: 0（全部已补）。抽查若干页：frontmatter 多一行合理 `摘要`，`git diff` 显示正文未动。

- [ ] **Step 4: Commit（Zettelkasten repo）**

按 `/git` 规范提交：`docs(wiki): backfill 592 页 frontmatter 摘要（lint）`。大批量改动可单独成 commit，便于 review/回滚。
