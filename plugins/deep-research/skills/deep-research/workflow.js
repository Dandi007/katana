export const meta = {
  name: 'deep-research',
  description: 'BFS clue-driven multi-source research -> cited report (judgment-driven stop)',
  phases: [{ title: 'Setup' }, { title: 'Explore' }, { title: 'Triage' }, { title: 'Harvest' }, { title: 'Synthesize' }],
}

// args = { topic, topicPath, skillDir, sources, maxWidth, initialClues, models }
// topicPath is a logical wiki MCP path. No client-visible KB root is accepted.
let A
try { A = typeof args === 'string' ? JSON.parse(args) : (args || {}) }
catch { A = { topic: typeof args === 'string' ? args : '' } }

const { skillDir } = A
const SOURCES = (A.sources && typeof A.sources === 'object' && !Array.isArray(A.sources)) ? A.sources : {}
const TPL = skillDir ? `${skillDir}/templates` : 'templates'
const MAX_WIDTH = (Number.isInteger(A.maxWidth) && A.maxWidth > 0) ? A.maxWidth : 10
const MAX_DEPTH = 3
const SAFETY_CAP = 50

const VALID_MODELS = new Set(['opus', 'sonnet', 'haiku', 'fable'])
const M = (A.models && typeof A.models === 'object' && !Array.isArray(A.models)) ? A.models : {}
const pickModel = (value, fallback) => (typeof value === 'string' && VALID_MODELS.has(value)) ? value : fallback
const WORKER_MODEL = pickModel(M.worker, 'sonnet')
const TRIAGE_MODEL = pickModel(M.triage, 'opus')
const SYNTH_MODEL = pickModel(M.synth, 'opus')
const HARVEST_MODEL = pickModel(M.harvest, 'haiku')

const topic = (typeof A.topic === 'string' && A.topic.trim()) ? A.topic.trim()
  : (typeof args === 'string' ? args.trim() : 'Unknown Topic')
const dirName = topic.replace(/[/\\:*?"<>|]/g, '-').replace(/\s+/g, ' ').trim().slice(0, 80) || 'Unknown Topic'
const requestedTopicPath = (typeof A.topicPath === 'string' && A.topicPath.trim()) ? A.topicPath.trim().replace(/^\/+|\/+$/g, '') : ''
if (requestedTopicPath && (!requestedTopicPath.startsWith('DeepThought/') || requestedTopicPath.includes('..'))) {
  throw new Error('topicPath must be a logical path below DeepThought/')
}
const topicPath = requestedTopicPath || `DeepThought/${dirName}`
const findingsPath = `${topicPath}/findings`
const norm = clue => (clue.text || '').trim().toLowerCase()

const coverage = findings => findings
  .flatMap(finding => (finding.reports || []).map(report => `- [${finding.clue_id}][${report.source}] ${report.digest} (${report.evidence_credibility})`))
  .join('\n')
  .slice(0, 4000)

const REPORT_SCHEMA = {
  type: 'object',
  required: ['source', 'anchor', 'evidence_credibility', 'digest', 'l2_file'],
  properties: {
    title: { type: 'string' },
    source: { type: 'string' },
    anchor: { type: 'string' },
    evidence_credibility: { type: 'string', enum: ['high', 'medium', 'low', 'conflicted'] },
    digest: { type: 'string' },
    entities: { type: 'array', items: { type: 'string' } },
    l2_file: { type: 'string' },
  },
  additionalProperties: false,
}

const FINDING_SCHEMA = {
  type: 'object',
  required: ['clue_id', 'status', 'reports', 'signals', 'new_clues'],
  properties: {
    clue_id: { type: 'string' },
    status: { type: 'string', enum: ['completed', 'partial', 'blocked'] },
    reports: { type: 'array', items: REPORT_SCHEMA },
    signals: {
      type: 'object',
      required: ['hit_original_keyword', 'high_density_source', 'time_author_aligned'],
      properties: {
        hit_original_keyword: { type: 'boolean' },
        high_density_source: { type: 'boolean' },
        time_author_aligned: { type: 'boolean' },
      },
      additionalProperties: false,
    },
    new_clues: {
      type: 'array',
      items: {
        type: 'object',
        required: ['text', 'why', 'suggested_sources', 'depth'],
        properties: {
          text: { type: 'string' },
          why: { type: 'string' },
          suggested_sources: { type: 'array', items: { type: 'string' } },
          depth: { type: 'number' },
        },
      },
    },
    blocked: {
      type: 'array',
      items: {
        type: 'object',
        properties: { source: { type: 'string' }, reason: { type: 'string' } },
      },
    },
  },
}

const TRIAGE_SCHEMA = {
  type: 'object',
  required: ['selected', 'converged'],
  properties: {
    selected: {
      type: 'array',
      items: {
        type: 'object',
        required: ['text', 'why', 'suggested_sources', 'depth', 'rationale'],
        properties: {
          text: { type: 'string' },
          why: { type: 'string' },
          suggested_sources: { type: 'array', items: { type: 'string' } },
          depth: { type: 'number' },
          rationale: { type: 'string' },
          local: { type: 'boolean' },
          id: { type: 'string' },
        },
      },
    },
    dropped: {
      type: 'array',
      items: {
        type: 'object',
        properties: { text: { type: 'string' }, reason: { type: 'string' } },
      },
    },
    converged: { type: 'boolean' },
  },
}

function sourceHints() {
  const names = Object.keys(SOURCES)
  if (!names.length) return ''
  return `
- 命名源：${names.map(name => `${name} -> ${SOURCES[name]}`).join('；')}
  · entry 是 /retrieval:<source> 入口；调用该入口检索，不把 entry 当文件路径。
  · 入口不可用、无凭证或零命中时，用 blocked 如实上报，不得编造。
  · 平台源 anchor 使用消息、文档、issue 或 MR 的 URL/唯一标识。`
}

function workerPrompt(clue, round) {
  const suggestedSources = clue.suggested_sources || []
  return `TASK: 针对线索 "${clue.text}" 收集证据。建议起点 source: ${suggestedSources.join(', ') || '自行判断'}。

MUST DO:
- wiki 已迁移内容通过 wiki_search 检索，并用 wiki MCP fs_read 读取返回的逻辑路径；工作记录通过 wf_search 检索并用 work-folder MCP fs_read 深挖。仅未迁本地子树可走 /retrieval:search-note|code。外部源优先调用 /retrieval:<source>，不可用时才用 WebSearch/WebFetch 或平台只读 CLI。${sourceHints()}
- 按 suggested_sources 每个源单独探索并生成一个 per-source 文件：
  · 逻辑路径："${findingsPath}/r${round}-c${clue.id}__<源名>.md"
  · 按 "${TPL}/finding.md" 模板生成内容，通过 wiki MCP fs_write 写入；frontmatter 填 source/anchor/evidence_credibility/digest/entities。
  · body 是该源的 L1 表和 L2 逐字摘录，每段必须带 anchor。
- 返回 FINDING_SCHEMA：reports[] 每源一条，含 source/anchor/evidence_credibility/digest/l2_file；l2_file 使用上述逻辑路径。

MUST NOT:
- 不跨源综合；除 wiki MCP 写本研究的 findings 外不做 mutation，不发消息、不评论 issue/MR、不 push。`
}

function triagePrompt(fresh, round) {
  return `你是本轮探索的 triage 判断脑。研究主问题："${topic}"。

已覆盖的发现（节选）：
${coverage(L1) || '（暂无）'}

本轮新线索（已去重）：
${JSON.stringify(fresh, null, 2)}

1) 从新线索挑下一轮 frontier，按重要性排序并给 rationale；不值得的放 dropped。
2) converged 只判断主问题是否足以回答或 frontier 是否枯竭，不得因轮数或成本停止。
3) 按 "${TPL}/clue_board.md" 生成快照，通过 wiki MCP fs_write 覆盖 "${topicPath}/clue_board.md"。

返回 TRIAGE_SCHEMA。`
}

function harvesterPrompt() {
  return `TASK: 汇编索引。使用 wiki MCP fs_glob(pattern="${findingsPath}/*.md") 列出 per-source finding，再逐个 fs_read 读取 frontmatter。

提取 clue_id / round / source / anchor / evidence_credibility / digest / 逻辑路径，汇编为 Markdown 表格：

| clue_id | round | source | evidence_credibility | digest | path |
|---------|-------|--------|----------------------|--------|------|

通过 wiki MCP fs_write 写入 "${findingsPath}/index.md"，末尾追加文件数和时间戳。除 index.md 外不修改任何文件。`
}

function synthesisPrompt() {
  return `探索已收敛。研究主问题："${topic}"。研究逻辑路径："${topicPath}"。

1) 用 wiki MCP fs_read 读取 "${findingsPath}/index.md"。
2) 按 evidence_credibility 和 relevance 选择高价值行，再用 fs_read 读取对应 L2；不要盲读全部文件。
3) 按 "${TPL}/sources.md" 生成 sources.md，保留 anchor + evidence_credibility。
4) 按 "${TPL}/topics.md" 生成 topics.md，聚类为 3-7 个主题并标 [^N] 引用。
5) 按 "${TPL}/report.md" 生成 report.md，确保每个论断可回溯，回填 sources.md 的 Used In。
6) 所有最终产物通过 wiki MCP fs_write 写到 "${topicPath}/"。

不得编造来源或修改本研究目录之外的 wiki 内容。返回 Executive Summary 和 3-5 条 Key Takeaways。`
}

const SETUP_SCHEMA = {
  type: 'object',
  required: ['initialClues'],
  properties: {
    initialClues: {
      type: 'array',
      minItems: 1,
      items: {
        type: 'object',
        required: ['id', 'text', 'local', 'suggested_sources', 'depth'],
        properties: {
          id: { type: 'string' },
          text: { type: 'string' },
          local: { type: 'boolean' },
          suggested_sources: { type: 'array', items: { type: 'string' } },
          depth: { type: 'number' },
        },
      },
    },
  },
}

const needsClues = !Array.isArray(A.initialClues) || !A.initialClues.length
phase('Setup')
const setup = await agent(
  `1. 用 wiki MCP fs_create 创建逻辑目录 "${topicPath}" 和 "${findingsPath}"（已存在视为成功）。\n` +
  (needsClues
    ? `2. 把研究主题 "${topic}" 拆成 3-6 条初始线索。wiki 知识线索 suggested_sources=["wiki"]，工作记录用 ["work-folder"]，外部源用已声明 retrieval 源名。返回 SETUP_SCHEMA。`
    : `2. 返回 SETUP_SCHEMA，initialClues 原样使用：${JSON.stringify(A.initialClues)}`),
  { phase: 'Setup', schema: SETUP_SCHEMA, label: needsClues ? 'setup:create+clues' : 'setup:create' },
)
const initialClues = needsClues ? setup.initialClues : A.initialClues

const seen = new Set(initialClues.map(norm))
let frontier = initialClues
let round = 0
const L1 = []

while (frontier.length) {
  if (round >= SAFETY_CAP) { log(`hit SAFETY_CAP=${SAFETY_CAP}, forcing stop`); break }
  round++
  const found = (await parallel(
    frontier.slice(0, MAX_WIDTH).map(clue => () => agent(workerPrompt(clue, round), {
      phase: 'Explore',
      schema: FINDING_SCHEMA,
      model: WORKER_MODEL,
      agentType: 'general-purpose',
      label: `explore:r${round}-${clue.id}`,
    })),
  )).filter(Boolean)
  L1.push(...found)

  const fresh = found.flatMap(finding => finding.new_clues || []).filter(clue => !seen.has(norm(clue)))
  fresh.forEach(clue => seen.add(norm(clue)))
  const picked = await agent(triagePrompt(fresh, round), { phase: 'Triage', schema: TRIAGE_SCHEMA, model: TRIAGE_MODEL })
  if (picked.converged) break
  frontier = (picked.selected || []).filter(clue => (clue.depth ?? round) <= MAX_DEPTH).slice(0, MAX_WIDTH)
}

phase('Harvest')
await agent(harvesterPrompt(), {
  phase: 'Harvest',
  model: HARVEST_MODEL,
  agentType: 'general-purpose',
  label: 'harvest:index',
})

phase('Synthesize')
return await agent(synthesisPrompt(), { phase: 'Synthesize', model: SYNTH_MODEL })
