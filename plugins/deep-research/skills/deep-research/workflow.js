export const meta = {
  name: 'deep-research',
  description: 'BFS clue-driven multi-source research → cited report (judgment-driven stop)',
  phases: [{ title: 'Explore' }, { title: 'Triage' }, { title: 'Synthesize' }],
}

// args = { topic, topicDir, skillDir, initialClues: [{ id, text, local, suggested_sources, depth }] }
// ⚠️ spike 实测：args 到脚本是 JSON 字符串，必须 parse（防御式兼容对象/字符串）
const A = typeof args === 'string' ? JSON.parse(args) : args
const { topic, topicDir, initialClues, skillDir } = A
const TPL = skillDir ? `${skillDir}/templates` : 'templates'  // 模板目录：未传 skillDir 时退回 CWD 相对路径

const MAX_WIDTH = 6      // 每轮 fan-out 宽度上限（形状护栏，不决定停不停）
const MAX_DEPTH = 3      // 单条线索最大深度（形状护栏）
const SAFETY_CAP = 50    // runaway backstop，正常碰不到；命中即 log 告警不静默截断

const norm = c => (c.text || '').trim().toLowerCase()
const coverage = L1 => L1
  .flatMap(f => (f.findings || []).map(x => `- [${f.clue_id}] ${x.title}`))
  .join('\n')
  .slice(0, 4000)   // 给 triage 的「已覆盖」摘要，控制长度

const FINDING_SCHEMA = {
  type: 'object',
  required: ['clue_id', 'status', 'findings', 'signals', 'new_clues', 'l2_file'],
  properties: {
    clue_id: { type: 'string' },
    status: { type: 'string', enum: ['completed', 'partial', 'blocked'] },
    findings: { type: 'array', items: { type: 'object',
      required: ['title', 'anchor', 'source_type', 'summary', 'credibility'],
      properties: {
        title: { type: 'string' }, anchor: { type: 'string' }, source_type: { type: 'string' },
        summary: { type: 'string' },
        credibility: { type: 'string', enum: ['high', 'medium', 'low', 'conflicted'] },
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
    l2_file: { type: 'string' },
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

function workerPrompt(clue, round) {
  return `TASK: 针对线索 "${clue.text}" 收集证据。建议起点 source: ${(clue.suggested_sources || []).join(', ') || '自行判断'}。

MUST DO:
- 本地线索：优先遵循知识库 CLAUDE.md/AGENTS.md 声明的检索约定；无约定时用 Grep/Glob/Read 直接检索。web 线索：用 WebSearch/WebFetch。
- 把【L2 原文】写入文件 "${topicDir}/findings/r${round}-c${clue.id}.md"，严格按 "${TPL}/finding.md" 模板：
  · L2 只摘与线索相关的段落，逐字保留，每段必带 anchor（URL/路径/file:line）；不相关的不塞、不整页 dump。
- 返回结构化结果（FINDING_SCHEMA）：clue_id="${clue.id}"；findings（每条含 anchor+summary+credibility）；
  signals 三个布尔据事实诚实上报；new_clues（3-8 条，depth=${round}）；l2_file 填上面的路径。

MUST NOT:
- 不写结论、不跨源综合；不修改 findings/ 以外任何文件；不做任何 mutation（不发消息/不开 issue/不 push）。`
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

function synthesisPrompt() {
  return `探索已收敛。研究主问题："${topic}"。素材目录："${topicDir}"。

按顺序生成最终产物（全部写入 ${topicDir}/）：
1) 读 ${topicDir}/findings/*.md 的【L2 原文】（这是真原始素材，必须读，别只凭记忆）。
2) sources.md：按 "${TPL}/sources.md" 模板合并去重，保留 anchor + credibility。
3) topics.md：按 "${TPL}/topics.md" 聚类为 3-7 个自包含主题，标 [^N] 引用 sources。
4) report.md：按 "${TPL}/report.md" 连贯叙事，note-seed 用 > [!note-seed] 标记，末尾附探索轨迹摘要；回填 sources.md 的 Used In 列。

MUST: 每个论断可回溯到 sources.md / L2 原文。MUST NOT: 编造来源、做任何 mutation。

返回一段 Executive Summary + 3-5 条 Key Takeaways（给对话收尾阶段展示用）。`
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
        agentType: 'general-purpose',   // worker 既要检索又要写 L2 文件；Explore 只读不能 Write
        label: `explore:r${round}-${clue.id}`,
      })
    )
  )).filter(Boolean)
  L1.push(...found)
  // worker 已把 L2 原文+锚点 写进 findings/r{round}-c{id}.md（脚本不读）

  // 🔒 dedup 新线索（纯代码）
  const fresh = found.flatMap(f => f.new_clues || [])
                     .filter(c => !seen.has(norm(c)))
  fresh.forEach(c => seen.add(norm(c)))

  // 🎨 主判断节点：triage agent 判断「是否收敛/该停」+ 从 fresh 选下一轮 frontier
  const picked = await agent(triagePrompt(fresh, round), { phase: 'Triage', schema: TRIAGE_SCHEMA })
  // triage agent 已在其任务内重写 clue_board.md 快照（脚本无 FS 权限）

  if (picked.converged) break         // 停止 = 判断驱动，绝不因成本/轮数停
  // 🔒 护栏只管形状：深度/宽度，不管停不停
  frontier = (picked.selected || [])
    .filter(c => (c.depth ?? round) <= MAX_DEPTH)
    .slice(0, MAX_WIDTH)
}

phase('Synthesize')
return await agent(synthesisPrompt(), { phase: 'Synthesize' })
