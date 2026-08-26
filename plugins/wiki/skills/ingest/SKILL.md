---
name: ingest
description: DEPRECATED 2026-08-27 — the wiki moved to wiki-v3 MCP. Writing now goes through line-level primitives (`wiki_page_create` / `append` / `insert` / `replace_lines` / `delete_lines`), not this pipeline. Kept only as a signpost.
---

# Ingest

> **已退役（2026-08-27）。** wiki 域整体切到 wiki-v3 MCP，写入改走**行级原语**：
> 
> - 新建 `wiki_page_create`
> - 改动 `wiki_page_append` / `wiki_page_insert` / `wiki_page_replace_lines` / `wiki_page_delete_lines`
>   —— **除新建外不整篇覆盖**；先 `wiki_page_get` 拿 `revision` 传回去做 CAS，被拒就重读再试
> - frontmatter 用 `wiki_page_set_meta`（字段名用中文：摘要/类型/tags/sources）
> 
> 治理不在 skill 里了，在写门本身：跨进程锁 + 原子写（临时文件 + rename）+ CAS + 语义化 commit + push 作为事务收尾。
> 
> 本文件保留只是为了让误调用落在这条说明上；下面的内容是历史实现，不要再照做。
> 

The single write pipeline. Every page, link, index entry, and log line the wiki
gains is created here — never by a bare `Write`. This pipeline is where the four
failure modes of AI-maintained knowledge (mediocrity collapse, model collapse,
cognitive offloading, container-boundary erosion) are held back: provenance,
mandatory linking, zone write policy, and the resist-table all live in these steps.

The wiki is server-owned. Use wiki MCP logical paths only; never resolve its
physical root from the client. Run `date "+%Y-%m-%d %H:%M"` before timestamped
steps — use that real time.

## Input — four shapes

Identify which one you have before starting:

1. **Inbox file** — a file already sitting in the wiki's inbox dir.
2. **Explicit path** — the user names a local file to ingest.
3. **URL** — a web source to fetch.
4. **Conversation capture** — "put what we just discussed about X into the wiki."
   The source is this conversation; no external file exists yet.

Then run the eight steps in order.

## 1. Read schema

Use wiki MCP `fs_read(path="WIKI.md")` — it is the contract, not optional.
- Determine the target **zone** from **§2** (path, purpose, naming).
- Pull that zone's **write policy**, **page template**, and **naming rule** (§2).
  If the schema **dispatches templates by a frontmatter field** (e.g. a `type`/`类型`
  field — one zone holding several document genres), resolve the specific template
  via that field instead of assuming one template per zone, and follow the schema's
  declared dispatch rule.
- A source may span zones: zone is decided per knowledge unit (in §4), not per
  source. When a unit fits two zones, prefer the more restrictive write policy
  (propose over autonomous).
- Internalize **§3 Page Conventions**: required frontmatter, the provenance rule
  (every claim traces to a listed source), and the link rules — including that
  **referenced pages get back-updated with a reciprocal link**.
- Read **§4 Create-vs-Update Criteria** and **§5 Ingest Specifics** — §5 declares
  this library's source types and any special handling (raw path, etc.).
Do not assume defaults; the schema overrides this skill where they differ.

## 2. Read source

- **Inbox:** use wiki MCP `fs_read`; an explicit external client file may use the
  client reader because it is an ingest input, not server-owned wiki storage.
- **URL:** fetch and save a copy into the raw layer first (the logical path
  declared in schema §5; if none, `raw/`) through `wiki_ingest_plan` →
  `wiki_ingest_apply`, then use `fs_read` for the **saved copy**. This keeps
  raw immutable and provenance linkable — never ingest straight from a live URL.
  - **Prefer retrieval adapters when available:** if a retrieval plugin exposes `/retrieval:*` sources, fetch external sources through the matching one (tweets→`/retrieval:twitter`, reddit→`/retrieval:reddit`, repos→`/retrieval:code`/`/retrieval:github`) to inherit their fallback ladders and credibility — then save that result to the raw layer. Fall back to direct fetch if no retrieval plugin is installed.
- **Conversation capture:** the relevant turns are the source; note them for §8 provenance.

If the source cannot be read (missing file, failed fetch, empty content), stop and
report — never propose from partial or imagined content.

## 3. Orient

Find the candidate set of existing pages this content touches:
- Use `wiki_search` with the content's key terms **and their synonyms**.
- Use `fs_read` for the returned index/MOC and candidate logical paths.
List each candidate with its relation to the new content: **will update** /
**will link** / **unrelated**. This is the comparison pass that prevents duplicate pages.

An empty candidate set is valid (brand-new topic): the minimum outlink is the
index/MOC itself; never fabricate links.

## 4. Judge units

Split the source into discrete **knowledge units**. Run each unit through the
create-vs-update criteria — **read `references/create-vs-update.md`** and apply it
unit by unit (update first; create only when all three conditions hold; record
merge candidates and skips). Do not eyeball it.

## 5. Build the proposal package

Assemble the full package, then submit it to `wiki_ingest_plan` before applying:
- **New-page drafts** — each following the zone's page template (or, when the
  schema dispatches templates by a frontmatter field, the template that field
  selects) + required frontmatter (§3), `sources:` populated per `references/provenance.md`.
  当 §3 声明了 per-page summary 字段（如 `摘要`），draft 的 frontmatter
  必须含一行该摘要：一句话、≤~40 字、描述本页讲什么 + 核心结论，从本页内容生成。
- **Existing-page diffs** — including the **reciprocal back-links** on every page a
  new page references (schema §3 rule — do this, don't skip it). When new content
  disagrees with an existing page, annotate both sides with the same `> [!conflict]`
  callout format defined by `/wiki:lint` (named disagreement + wikilink to the other
  page) — prose-only annotations drift and are invisible to mechanical checks.
- **Link plan** — every new page carries **≥1 outlink** (no islands). If the
  candidate set was empty, the minimum outlink is the index/MOC (or a parent MOC
  stub built per schema); never fabricate links.
- **Index / MOC diff** — new pages registered so query can reach them.

### Proposal item format (one block per item)

```
[CREATE|UPDATE] <page path> (zone: <zone>)
  why: <one line — which criterion fired>
  sources: <provenance refs>
  outlinks: <pages this links to>
  back-updates: <existing pages getting reciprocal links>
  diff/draft: <content or diff>
```

Plus one final item: `[INDEX] <index/MOC diff>`. In non-interactive mode (§7),
"output the full proposal text" means emit exactly this set of blocks.

## 6. Resist-table self-check

**Read `references/resist-table.md`** and walk every row against your proposal. If
you catch yourself leaning on any excuse in that table, revise the package before
proceeding. This step is mandatory, not advisory.

## 7. Apply per zone write policy

- **autonomous** zone → apply the accepted plan with `wiki_ingest_apply`.
- **propose** zone → present the full proposal package and confirm each item via
  AskUserQuestion before writing.
- **Non-interactive (`claude -p`):** AskUserQuestion is unavailable. In a propose
  zone, write **only** if the prompt explicitly pre-authorizes it (e.g. "本次提案视为已批准"
  / "proposals pre-approved"). Otherwise do not write any page — output the full
  proposal text and you MUST still append the log line
  `## [YYYY-MM-DD HH:MM] ingest | proposed (not applied): <source>` to `log.md`
  using wiki MCP `fs_edit` — skipping this journaling is a pipeline violation, then stop.

## 8. Apply + record

- Apply all approved files (new pages, updated pages, index/MOC) through
  `wiki_ingest_apply`; the server enforces provenance/outlink/frontmatter invariants.
- **Inbox archival:** include `fs_rename` from `inbox/<file>` to the raw logical
  path in the approved plan. In propose zones without authorization, leave inbox untouched.
- Ensure the plan records in `log.md`:
  `## [YYYY-MM-DD HH:MM] ingest | <source>` followed by body lines listing every
  **created** and **updated** page.
- Commit policy is server-governed; never run client git operations on wiki storage.

## Boundary

This skill **writes**. It does not answer questions (that's `/wiki:query`) and
does not run a whole-wiki health check (that's `/wiki:lint`). Provenance format
details live in `references/provenance.md`.
