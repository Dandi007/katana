---
name: lint
description: DEPRECATED 2026-08-27 — the wiki moved to wiki-v3 MCP. Use `wiki_lint_broken_links` for the mechanical pass. Kept only as a signpost.
---

# Lint

> **已退役（2026-08-27）。** wiki 域整体切到 wiki-v3 MCP。
> 
> 机械体检用 `wiki_lint_broken_links`（按被引用次数排出「该补哪一页」），反链用 `wiki_page_backlinks`，整体统计用 `wiki_stats`。
> 
> 本文件保留只是为了让误调用落在这条说明上；下面的内容是历史实现，不要再照做。
> 

The adversarial pass. The headline failure of AI-maintained knowledge is
**mediocrity collapse** — *left ungoverned, the compiler does not think; it
smooths*, flattening disagreement into bland consensus. Lint exists to fight
that. Its core is the schema's **antagonist rule** (§7): *contradictions must be
named as disagreements and cross-annotated on both pages — never smoothed into
consensus.* Lint surfaces tension; it does not resolve it away.

The wiki is server-owned. Use wiki MCP logical paths only; never resolve or scan
its physical root from the client. Run `date "+%Y-%m-%d %H:%M"` before any
timestamped step (report path, log) — use that real time.

## 0. Scope

Use `fs_read(path="WIKI.md")` (wiki MCP) to load **§7** (stale threshold,
exemptions) and §2 (zones, write policy) — the contract. Then pick scope:

- **Full** — every page.
- **Zone** — one zone's pages.
- **Incremental** — only pages changed since the last lint. The server derives
  the baseline from `log.md`; first run or an invalid baseline falls back to full.

Ask the user (AskUserQuestion) when scope is unstated. **Non-interactive** →
default **incremental** (full on first run).

## 1. Mechanical checks

Run `wiki_lint_mechanical(scope=<full|zone|incremental>)`. This server-side tool
owns deterministic enumeration, pruning, timestamps and schema checks. Do not
reimplement it with client shell commands. Use `fs_glob` to list a returned
logical scope and `fs_read` to inspect pages needed for semantic judgment.

- **Orphan pages** — consume the mechanical orphan result.
- **Broken links** — consume the mechanical broken-link result.
- **Missing required frontmatter** — consume schema §3 findings (e.g.
  `created`, `sources`, `tags`).
  当 §3 声明了 per-page **summary 字段**（一行自描述摘要，如本库的 `摘要`）时，
  缺它的页同样计入本检查 —— 它是 backfill-class finding（修复见 §4），不是单纯报告项。
- **Index/MOC consistency** — consume the server's index consistency result.
- **Naming violations** — consume per-zone naming results. If §2 has no
  machine-checkable rule, note the server's skipped result in the report.

Drop any hit covered by the §7 exemption list before reporting it.

## 2. Semantic checks (LLM judgment)

- **Contradiction pairs (antagonist rule)** — two pages disagree on the same
  fact/judgment. **Narrow to candidate pairs first — never attempt full pairwise
  comparison:** compare only pages that share ≥1 tag, are mutually linked, are
  co-cited by the same source, or are named together in a `merge-candidate` log
  line. **Name the disagreement** in one sentence (what exactly differs).
  Produce a `> [!conflict]` callout for **each** page, carrying a wikilink to the
  other and the named disagreement. **Never rewrite either side to agree** — the
  callouts are the deliverable, not a reconciliation.
- **Stale claims** — a claim superseded by a newer source (or a page past the §7
  stale threshold whose sources moved). Annotate stale: keep the original text,
  note what supersedes it. **Never delete.**
- **Missing pages** — a concept referenced by **≥3 pages** with no page of its
  own → list as a create candidate. Do not build it here (that is ingest).
- **Provenance gaps** — an important claim with no backing `source` → list it.
- **Merge candidates** — consume every `merge-candidate: <A> <B>` line logged by
  ingest, plus overlapping page pairs you find → give a merge recommendation. Do
  not merge here (that is ingest).

## 3. Report

Use wiki MCP `fs_create` for `.wiki/lint` if needed and `fs_write` to write
`.wiki/lint/YYYYMMDD-HHMM.md` from `templates/lint-report.md`. Every report
section has an empty state — write `— none —` when a section has no findings.
Then use `fs_edit` to append to `log.md`:
`## [YYYY-MM-DD HH:MM] lint | <scope>: <N issues>`. A clean run is still logged —
N=0 → `## [YYYY-MM-DD HH:MM] lint | full: 0 issues — clean`.

## 4. Fix proposals

Per finding, propose a fix and route it through the zone's write policy (schema §2):

- **autonomous** zone → apply the fix with wiki MCP `fs_edit`/`fs_write`.
- **propose** zone → present each fix via AskUserQuestion and, after confirmation,
  apply it with wiki MCP `fs_edit`/`fs_write`.

**Lint may apply only:** conflict/stale annotations, broken-link fixes, index
entry back-fills, **and summary-field backfill** (filling a missing
schema-declared per-page summary line). Anything else — building a page, merging
pages, rewriting content — is **handed to `/wiki:ingest`** or listed as a human
to-do. Never do it here.

### Summary-field backfill（schema-declared summary 字段专属）

A schema-declared per-page **summary** field (one line, self-describing, e.g.
`摘要`) is **derived-from-self metadata** — a compression of content already on
the page, not new knowledge. The model-collapse defense (raw-immutability /
no-re-ingesting-synthesis) does not apply, and the field never touches the body.
So lint MAY generate and write it, governed as follows instead of per-fix propose:

1. **Generate from the page itself** — use `fs_read` for the full page, write one line (≤~40
   chars per schema §3): what the page is about + its core definition/claim.
   Never invent beyond the page; never copy a whole paragraph.
2. **Insert into frontmatter only** — use `fs_edit` to add the summary line (e.g. `摘要:`) to the
   page's frontmatter block. **Never edit the body** (the body bytes must stay
   identical). Skip pages that already have a non-empty summary.
3. **Batch governance — sampling QC, then autonomous batch** (**this overrides the
   propose-zone per-fix AskUserQuestion rule above, for this backfill type only**):
   when backfilling many pages (e.g. a first full-library run), generate a **random sample of N=10**
   first and show them for human QC of quality. On approval, **write all remaining
   pages autonomously — do NOT AskUserQuestion per page** (that does not scale to
   hundreds). A wrong summary is cheap to regenerate (rerun lint). In
   non-interactive mode, only run the batch if the prompt pre-authorizes it.
4. **Scale via Workflow when large:** for a big backfill, fan out summarizers
   (one agent uses `fs_read` for one page → returns its summary line); apply the returned lines with `fs_edit`.
   raw / inbox zones are exempt.

**Non-interactive (`claude -p`):** same as ingest — in a propose zone, apply a fix
only if the prompt pre-authorizes it; otherwise the report is the endpoint. Append
the log line with `fs_edit` regardless.

## Boundary

This skill **diagnoses and annotates**. It does not build knowledge pages or merge
pages (that is `/wiki:ingest`) and does not answer questions (that is `/wiki:query`).
The antagonist rule is absolute: contradictions are named, never smoothed.
