export const meta = {
  name: 'deep-research',
  description: 'BFS clue-driven multi-source research → cited report (judgment-driven stop)',
  phases: [{ title: 'Setup' }, { title: 'Explore' }, { title: 'Triage' }, { title: 'Harvest' }, { title: 'Synthesize' }],
}

// args = { topic, topicDir, kbDir, skillDir, sources, maxWidth, initialClues, models }
// Robust: handles structured object, valid JSON string, or raw topic string (Stage A bypassed)
let A
try { A = typeof args === 'string' ? JSON.parse(args) : (args || {}) }
catch { A = { topic: typeof args === 'string' ? args : '' } }

const { skillDir } = A
const SOURCES = (A.sources && typeof A.sources === 'object' && !Array.isArray(A.sources)) ? A.sources : {}  // 命名源 { name: entry }；entry=入口文档路径(相对KB根)或裸命令名
const TPL = skillDir ? `${skillDir}/templates` : 'templates'  // 模板目录：未传 skillDir 时退回 CWD 相对路径

const MAX_WIDTH = (Number.isInteger(A.maxWidth) && A.maxWidth > 0) ? A.maxWidth : 10  // 每轮 fan-out 宽度上限（deep_research_max_width 可配；形状护栏，不决定停不停）
const MAX_DEPTH = 3      // 单条线索最大深度（形状护栏）
const SAFETY_CAP = 50    // runaway backstop，正常碰不到；命中即 log 告警不静默截断

// 四档 agent 模型（deep_research_models 可配；启动前由主 agent 按 topic 意图定好，workflow 内每轮不变）
// 非法/缺省回退默认（防御式）：worker 量大成本敏感→sonnet；harvest haiku（扫文件轻量）；triage 判收敛+选 frontier、synth 写终稿→opus
const VALID_MODELS = new Set(['opus', 'sonnet', 'haiku', 'fable'])
const M = (A.models && typeof A.models === 'object' && !Array.isArray(A.models)) ? A.models : {}
const pickModel = (v, d) => (typeof v === 'string' && VALID_MODELS.has(v)) ? v : d
const WORKER_MODEL = pickModel(M.worker, 'sonnet')
const TRIAGE_MODEL = pickModel(M.triage, 'opus')
const SYNTH_MODEL = pickModel(M.synth, 'opus')
const HARVEST_MODEL = pickModel(M.harvest, 'haiku')

// KB root — from Stage A, which resolves an ABSOLUTE kbDir against the katana
// KB-root anchor (env > .katana value > dir-of-.katana > CLAUDE_PROJECT_DIR > pwd).
// When Stage A is bypassed (raw string input, no A.kbDir), the Setup agent below
// resolves the same way inline (G12.2: absolute, not JS-expanded env / cwd).
// '.' here is only the pre-resolution placeholder; overridden post-Setup via _setup.kbDir.
let KB_DIR = (typeof A.kbDir === 'string' && A.kbDir.trim()) ? A.kbDir.trim() : '.'

// topic — from structured args, or raw string input when Stage A was bypassed
const topic = (typeof A.topic === 'string' && A.topic.trim()) ? A.topic.trim()
            : (typeof args === 'string' ? args.trim() : 'Unknown Topic')

// topicDir — from structured args, or auto-derived from topic + KB_DIR.
// `let` so the bypass path can recompute it once KB_DIR is resolved post-Setup.
const _dirName = topic.replace(/[/\\:*?"<>|]/g, '-').replace(/\s+/g, ' ').trim().slice(0, 80)
const _topicDirGiven = (typeof A.topicDir === 'string' && A.topicDir.trim()) ? A.topicDir.trim() : ''
let topicDir = _topicDirGiven || `${KB_DIR}/DeepThought/${_dirName}`

const norm = c => (c.text || '').trim().toLowerCase()

// coverage() 重写：从 reports[] 聚合 [clue][source] digest (credibility)，信息密度高于 title-only
const coverage = L1 => L1
  .flatMap(f => (f.reports || []).map(r => `- [${f.clue_id}][${r.source}] ${r.digest} (${r.credibility})`))
  .join('\n')
  .slice(0, 4000)   // 给 triage 的「已覆盖」摘要，控制长度

// 回传契约：改为 reports[]（每源一张 header 卡）替代扁平 findings + 单 l2_file
const FINDING_SCHEMA = {
  type: 'object',
  required: ['clue_id', 'status', 'reports', 'signals', 'new_clues'],
  properties: {
    clue_id: { type: 'string' },
    status: { type: 'string', enum: ['completed', 'partial', 'blocked'] },
    reports: { type: 'array', items: { type: 'object',
      required: ['source', 'anchor', 'credibility', 'digest', 'l2_file'],
      properties: {
        title: { type: 'string' },
        source: { type: 'string' },
        anchor: { type: 'string' },
        credibility: { type: 'string', enum: ['high', 'medium', 'low', 'conflicted'] },
        digest: { type: 'string' },
        entities: { type: 'array', items: { type: 'string' } },
        l2_file: { type: 'string' },
      } } },
    signals: { type: 'object',
      required: ['hit_original_keyword', 'high_density_source', 'time_author_aligned'],
      properties: {
        hit_original_keyword: { type: 'boolean' },
        high_density_source: { type: 'boolean' },
        time_author_aligned: { type: 'boolean' },
      } },
    new_clues: { type: 'array', items: { type: 'object',
      required: ['text', 'why', 'suggested_sources', 'depth'],
      properties: {
        text: { type: 'string' }, why: { type: 'string' },
        suggested_sources: { type: 'array', items: { type: 'string' } },
        depth: { type: 'number' },
      } } },
    blocked: { type: 'array', items: { type: 'object',
      properties: { source: { type: 'string' }, reason: { type: 'string' } } } },
  },
}

const TRIAGE_SCHEMA = {
  type: 'object',
  required: ['selected', 'converged'],
  properties: {
    selected: { type: 'array', items: { type: 'object',
      required: ['text', 'why', 'suggested_sources', 'depth', 'rationale'],
      properties: {
        text: { type: 'string' }, why: { type: 'string' },
        suggested_sources: { type: 'array', items: { type: 'string' } },
        depth: { type: 'number' }, rationale: { type: 'string' },
        local: { type: 'boolean' }, id: { type: 'string' },
      } } },
    dropped: { type: 'array', items: { type: 'object',
      properties: { text: { type: 'string' }, reason: { type: 'string' } } } },
    converged: { type: 'boolean' },
  },
}

function sourceHints() {
  const names = Object.keys(SOURCES)
  if (!names.length) return ''
  return `
- 命名源（KB config 声明的平台源）：${names.map(n => `${n} → ${SOURCES[n]}`).join(' ； ')}
  · retrieval 收口：外部源（web/平台）优先经 /retrieval:<source>（retrieval plugin 已装时），
    无则 fallback 到 WebSearch/WebFetch / 平台 CLI；本地优先 KB 约定 /retrieval:search-note|code。
  · entry 是文档路径 → 先 Read 它（相对当前工作目录，即 KB 根；同目录如有 errors.md 一并先读避坑），按其指引做只读检索；entry 是裸命令名 → 直接用该 CLI 的只读子命令。
  · 入口不可用 / 无凭证 / 0 命中 → 用 blocked 字段如实上报（source+reason），不得编造。
  · 平台源 finding 的 anchor 用消息/文档/issue/MR 的 URL 或唯一标识；source_type 填 "platform:<源名>"。`
}

function workerPrompt(clue, round) {
  const suggestedSrcs = (clue.suggested_sources || [])
  return `TASK: 针对线索 "${clue.text}" 收集证据。建议起点 source: ${suggestedSrcs.join(', ') || '自行判断'}。

MUST DO:
- 外部源（web/平台）优先经 /retrieval:<source>（retrieval plugin 已装时），无则 fallback 到 WebSearch/WebFetch / 平台 CLI。本地优先遵循知识库 CLAUDE.md/AGENTS.md 声明的检索约定 /retrieval:search-note|code，无约定时用 Grep/Glob/Read 直接检索。${sourceHints()}
- 按线索的 suggested_sources，每个源单独探索并写一个 per-source 文件：
  · 文件路径格式："${topicDir}/findings/r${round}-c${clue.id}__<源名>.md"（双下划线 __ 分隔源名）
  · 每文件严格按 "${TPL}/finding.md" 模板，frontmatter 填 source/anchor/credibility/digest/entities；
    body 是该源的 L1 表 + L2 逐字摘录（只摘与线索相关段落，每段必带 anchor）。
- 返回结构化结果（FINDING_SCHEMA）：
  · clue_id="${clue.id}"
  · reports[]：每个探索过的源一条，含 source/anchor/credibility/digest/l2_file（l2_file=上面写的路径）
  · signals 三个布尔据事实诚实上报
  · new_clues（3-8 条，depth=${round}）

MUST NOT:
- 不写结论、不跨源综合；不修改 findings/ 以外任何文件；不做任何 mutation（不发消息/不回复群聊/不开或评论 issue·MR/不 push——平台源一律只读）。`
}

function triagePrompt(fresh, round) {
  return `你是本轮探索的 triage 判断脑。研究主问题："${topic}"。

已覆盖的发现（节选）：
${coverage(L1) || '（暂无）'}

本轮新线索（已去重）：
${JSON.stringify(fresh, null, 2)}

要做两件判断（都是语义判断，没有打分公式）：
1) 从新线索里挑出值得下一轮追的，按重要性排序，每条给 rationale；不值得的放 dropped 并说明理由。
   参考证据：是否命中主问题、是否来自高密度源、是否多个 worker 指向——作为判断依据，不是加权求和。
   给每条 selected 补 id（如 c${round}_0）、local（是否本地源）、depth。
2) converged：判断「主问题到此是否已可充分回答 / 边际收益是否递减」。是→true，循环就停；否→false。
   注意：**不要因为轮数多或成本高就 converge**，只看信息是否够回答主问题。若没有任何值得追的新线索，也置 converged=true。

并：把 clue_board 快照重写到 "${topicDir}/clue_board.md"，按 "${TPL}/clue_board.md" 模板（Frontier=本次 selected，Visited 追加本轮已探线索，每轮摘要追加一行含停止判断）。

返回 TRIAGE_SCHEMA。`
}

function harvesterPrompt() {
  return `TASK: 汇编索引 — 扫描所有 per-source finding 文件，提取 frontmatter，生成索引表写入 index.md。

1. 用 Bash/awk/grep 扫描 "${topicDir}/findings/" 下所有 *.md 文件的 frontmatter（--- 到 --- 之间）。
2. 提取每个文件的：clue_id / round / source / anchor / credibility / digest / 文件路径。
3. 把结果汇编为 Markdown 表格，写入 "${topicDir}/findings/index.md"：
   格式示例：
   \`\`\`
   # findings/index.md — harvest 索引

   | clue_id | round | source | credibility | digest | path |
   |---------|-------|--------|-------------|--------|------|
   | c0 | 1 | web | high | ... | findings/r1-c0__web.md |
   \`\`\`
4. 在文件末尾追加 harvest 元数据行（文件数、时间戳）。

MUST NOT: 不修改除 index.md 以外的任何文件。`
}

function synthesisPrompt() {
  return `探索已收敛。研究主问题："${topic}"。素材目录："${topicDir}"。

按顺序生成最终产物（全部写入 ${topicDir}/）：
1) 先 Read "${topicDir}/findings/index.md"（harvest 阶段已汇编的索引表），按 credibility/relevance 选择性读取高价值源的 L2 正文（不必盲读全部文件）。
2) 根据 index.md 索引，有选择地 Read 高 credibility 或与主问题最相关的 findings/*.md L2 原文（先高后低，跳过 low+不相关的，节省 context）。
3) sources.md：按 "${TPL}/sources.md" 模板合并去重，保留 anchor + credibility。
4) topics.md：按 "${TPL}/topics.md" 聚类为 3-7 个自包含主题，标 [^N] 引用 sources。
5) report.md：按 "${TPL}/report.md" 连贯叙事，note-seed 用 > [!note-seed] 标记，末尾附探索轨迹摘要；回填 sources.md 的 Used In 列。

MUST: 每个论断可回溯到 sources.md / L2 原文。MUST NOT: 编造来源、做任何 mutation。

返回一段 Executive Summary + 3-5 条 Key Takeaways（给对话收尾阶段展示用）。`
}

// Setup phase: always ensure topicDir/findings exists; generate initialClues when Stage A was bypassed
const SETUP_SCHEMA = {
  type: 'object', required: ['initialClues'],
  properties: {
    kbDir: { type: 'string' },   // absolute KB root resolved inline vs katana KB-root anchor (bypass path)
    initialClues: { type: 'array', minItems: 1, items: { type: 'object',
    required: ['id', 'text', 'local', 'suggested_sources', 'depth'],
    properties: {
      id: { type: 'string' }, text: { type: 'string' }, local: { type: 'boolean' },
      suggested_sources: { type: 'array', items: { type: 'string' } }, depth: { type: 'number' },
    } } } },
}

const _needsClues = !Array.isArray(A.initialClues) || !A.initialClues.length
// Stage A bypassed when no absolute kbDir was passed: Setup agent resolves the
// KB root inline against the katana KB-root anchor (G12.2: absolute, not cwd),
// then mkdir the resolved dir.
const _needsKb = !(typeof A.kbDir === 'string' && A.kbDir.trim())
if (_needsClues) log(`Stage A not provided — setup agent will create dirs and split clues`)
if (_needsKb) log(`No absolute kbDir passed — setup agent will resolve KB root inline against katana KB-root anchor`)
phase('Setup')

const _mkdirStep = _needsKb
  ? `1. 用 Bash 就地解析 KB 根（基准 katana KB-root 语义，绝对路径，非 cwd）并建目录。\n` +
    `   deep-research 插件不自带 katana-config 帮手，故内联解析：\n` +
    `   \`\`\`bash\n` +
    `   KATANA=""\n` +
    `   if [ -n "\${KATANA_CONFIG_FILE:-}" ]; then KATANA="$KATANA_CONFIG_FILE"\n` +
    `   elif [ -n "\${CLAUDE_PROJECT_DIR:-}" ] && [ -f "$CLAUDE_PROJECT_DIR/.katana" ]; then KATANA="$CLAUDE_PROJECT_DIR/.katana"\n` +
    `   elif [ -f "$HOME/.katana" ]; then KATANA="$HOME/.katana"; fi\n` +
    `   KBVAL="\${DEEP_RESEARCH_KB_DIR:-}"\n` +
    `   if [ -z "$KBVAL" ] && [ -n "$KATANA" ]; then KBVAL="$(awk -F= '$1=="deep_research_kb_dir"{v=substr($0,length($1)+2);sub(/#.*/,"",v);gsub(/^[[:space:]]+|[[:space:]]+$/,"",v);print v;exit}' "$KATANA")"; fi\n` +
    `   if [ -n "$KATANA" ]; then KBROOT="$(cd "$(dirname "$KATANA")" && pwd)"; else KBROOT="\${CLAUDE_PROJECT_DIR:-$(pwd)}"; fi\n` +
    `   case "$KBVAL" in ""|".") KB="$KBROOT";; "~") KB="$HOME";; "~/"*) KB="$HOME/\${KBVAL#~/}";; /*) KB="$KBVAL";; *) KB="$KBROOT/$KBVAL";; esac\n` +
    `   TOPIC_DIR="${_topicDirGiven ? _topicDirGiven : '$KB/DeepThought/' + _dirName}"\n` +
    `   mkdir -p "$TOPIC_DIR/findings"\n` +
    `   echo "$KB"   # 回填 SETUP_SCHEMA.kbDir（绝对 KB 根）\n` +
    `   \`\`\`\n` +
    `   把上面 echo 出的绝对 KB 根填进返回的 kbDir 字段。\n`
  : `1. 用 Bash 运行 \`mkdir -p "${topicDir}/findings"\`（幂等，目录已存在无害）。\n`

const _setup = await agent(
  _mkdirStep +
  (_needsClues
    ? `2. 把研究主题 "${topic}" 拆成 3-6 条初始搜索线索，格式：{ id:"c0", text:"...", local:false, suggested_sources:["web"], depth:0 }。\n` +
      `   local=true 仅当线索主要靠本地知识库而非 web 回答。\n` +
      `返回 SETUP_SCHEMA。`
    : `返回 SETUP_SCHEMA，initialClues 照搬：${JSON.stringify(A.initialClues)}`),
  { phase: 'Setup', schema: SETUP_SCHEMA, label: _needsClues ? 'setup:mkdir+clues' : 'setup:mkdir' }
)
const initialClues = _needsClues ? _setup.initialClues : A.initialClues

// Adopt the resolved absolute KB root from Setup (bypass path) and recompute topicDir.
if (_needsKb && _setup && typeof _setup.kbDir === 'string' && _setup.kbDir.trim()) {
  KB_DIR = _setup.kbDir.trim()
  if (!_topicDirGiven) topicDir = `${KB_DIR}/DeepThought/${_dirName}`
}

const seen = new Set(initialClues.map(norm))
let frontier = initialClues, round = 0
const L1 = []

while (frontier.length) {             // 唯一"自然"停止：没线索可探了
  if (round >= SAFETY_CAP) { log(`⚠️ hit SAFETY_CAP=${SAFETY_CAP}, forcing stop`); break }
  round++

  // 🔒 fan-out：每轮 barrier；🎨 每个 worker 怎么探/L1怎么写/信号怎么报 = 自由
  const found = (await parallel(
    frontier.slice(0, MAX_WIDTH).map(clue => () =>
      agent(workerPrompt(clue, round), {
        phase: 'Explore',
        schema: FINDING_SCHEMA,
        model: WORKER_MODEL,            // 检索档（默认 sonnet，deep_research_models 可配）
        agentType: 'general-purpose',   // worker 既要检索又要写 L2 文件；Explore 只读不能 Write
        label: `explore:r${round}-${clue.id}`,
      })
    )
  )).filter(Boolean)
  L1.push(...found)
  // worker 已把 L2 原文+锚点 写进 per-source 文件 findings/r{round}-c{id}__<source>.md

  // 🔒 dedup 新线索（纯代码）
  const fresh = found.flatMap(f => f.new_clues || [])
                     .filter(c => !seen.has(norm(c)))
  fresh.forEach(c => seen.add(norm(c)))

  // 🎨 主判断节点：triage agent 判断「是否收敛/该停」+ 从 fresh 选下一轮 frontier
  const picked = await agent(triagePrompt(fresh, round), { phase: 'Triage', schema: TRIAGE_SCHEMA, model: TRIAGE_MODEL })
  // triage agent 已在其任务内重写 clue_board.md 快照（脚本无 FS 权限）

  if (picked.converged) break         // 停止 = 判断驱动，绝不因成本/轮数停
  // 🔒 护栏只管形状：深度/宽度，不管停不停
  frontier = (picked.selected || [])
    .filter(c => (c.depth ?? round) <= MAX_DEPTH)
    .slice(0, MAX_WIDTH)
}

// Harvest phase: 汇编索引 — 扫 findings/*.md frontmatter → index.md
phase('Harvest')
await agent(harvesterPrompt(), {
  phase: 'Harvest',
  model: HARVEST_MODEL,
  agentType: 'general-purpose',   // 需要跑 Bash + Write
  label: 'harvest:index',
})
// harvest agent 已写 findings/index.md

phase('Synthesize')
return await agent(synthesisPrompt(), { phase: 'Synthesize', model: SYNTH_MODEL })
