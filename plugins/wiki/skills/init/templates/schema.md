# WIKI Schema

The schema is the product. Every library-level difference — zones, write policy,
page templates, link rules — lives here, not in plugin skills. Read this before
any `/wiki:*` operation.

## 1. Overview

- **Purpose:** {{PURPOSE}}
- **wiki_root:** {{WIKI_ROOT}}

Zones are defined in §2.

## 2. Zones

| Zone | Path | Purpose | Write policy | Page template | Naming |
|------|------|---------|--------------|---------------|--------|
| notes | `notes/` | thinking | propose | atomic card (one idea per page) | concrete-noun, kebab-case |

- **Purpose** ∈ `thinking` (exploratory, your reasoning) \| `executive` (durable, cited fact).
- **Write policy** ∈ `propose` (human gate: ingest drafts, human approves) \| `autonomous` (ingest writes directly). Default `propose`.
- **Page template** is normally one template per zone. When a single zone holds **multiple document genres** (e.g. atomic note vs source-analysis vs architecture), the template may instead be **dispatched by a frontmatter field** (e.g. a `type`/`类型` field): declare the dispatch rule and the per-type skeletons in a dedicated section (e.g. a §3a), then point this column at it. Ingest resolves the template via that field rather than one-per-zone. The dispatch *mechanism* is generic; the genres and their skeletons stay library-specific — they live here in the schema, never in plugin skills.

## 3. Page Conventions

- **Required frontmatter:** `created`, `sources`, `tags`.
- **`摘要`（推荐）：** 每页 frontmatter 一行自描述摘要（一句话，≤~40 字：这页讲什么 +
  核心结论）。供检索/关联走读只读 frontmatter 即可预览页面，不必翻正文。
  ingest 新建页生成、lint 可对存量 backfill。
- **Provenance:** every claim traces back to a `source` listed in frontmatter — no orphan assertions. `thinking` pages may hold un-sourced reasoning if labeled as such.
- **Link rules:** every page carries **≥1 outlink** (no islands). On ingest, pages this page references are **back-updated** with a reciprocal link.

## 4. Create-vs-Update Criteria

Prefer **update** over **create**. Only spin a new page when all hold:

1. **≥3 sentences** — less than that, it's a paragraph in an existing page.
2. **Concrete-noun test** — if you can't name it with one concrete noun, it isn't a page.
3. **Anti-cramming** — if a page would hold two distinct concepts, split instead of cram.

## 5. Ingest Specifics

- **Source types:** {{SOURCE_TYPES}} (e.g. chat, URL, pasted text, file).
- **Raw layer path:** {{RAW_PATH|default: raw/}} — immutable; URL sources are archived here before citing.
- Raw sources are immutable and archived; ingest never edits them in place.

## 6. Query Conventions

- **Citation format:** a wikilink to the page, or a `source` anchor for raw-backed claims.
- **Back-fill rule:** when answering synthesizes **≥2 pages** into a structure not present in any single page (a comparison table or a conclusion), propose writing that synthesis back via `/wiki:ingest`.

## 7. Lint Rules

- **Antagonist rule:** Contradictions must be named as disagreements and cross-annotated on both pages — never smoothed into consensus.
- **Stale threshold:** {{STALE_THRESHOLD|default: 180 days since last update}} — flag pages untouched beyond this whose sources have moved.
- **Exemptions:** {{LINT_EXEMPTIONS}} (paths/pages excused from a given rule).

## 8. Retrieval Augmentation

- **embedding:** none

(Optional: set an OpenAI-compatible `/v1/embeddings` endpoint to enable semantic retrieval in `/wiki:query`.)
