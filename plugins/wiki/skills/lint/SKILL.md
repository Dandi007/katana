---
name: lint
description: The wiki's periodic health check. Use whenever the user wants to audit / 体检 / lint the wiki, hunt contradictions, find orphan or stale pages, or check provenance and index consistency — in any language. Mechanical checks plus LLM semantic checks; reports findings and proposes fixes, but never builds pages or merges them (that is ingest).
---

# Lint

The adversarial pass. The headline failure of AI-maintained knowledge is
**mediocrity collapse** — *left ungoverned, the compiler does not think; it
smooths*, flattening disagreement into bland consensus. Lint exists to fight
that. Its core is the schema's **antagonist rule** (§7): *contradictions must be
named as disagreements and cross-annotated on both pages — never smoothed into
consensus.* Lint surfaces tension; it does not resolve it away.

`wiki_root` is injected by the using-wiki hook. Run `date "+%Y-%m-%d %H:%M"`
before any timestamped step (report path, log) — use that real time.

## 0. Scope

Read `<wiki_root>/WIKI.md` **§7** (stale threshold, exemptions) and §2 (zones,
write policy) — the contract. Then pick scope:

- **Full** — every page.
- **Zone** — one zone's pages.
- **Incremental** — only pages changed since the last lint. Find the baseline:
  `grep "^## \[" <wiki_root>/log.md | grep "| lint |"` → last lint timestamp;
  lint only pages modified after it (`git log --since` / `find -newermt` to
  enumerate). First run ever (no lint line) → fall back to **full**.

Ask the user (AskUserQuestion) when scope is unstated. **Non-interactive** →
default **incremental** (full on first run).

## 1. Mechanical checks

Deterministic — run grep/awk ad hoc, do not author a script. Examples use the
placeholder `<wiki_root>`:

- **Orphan pages** — a page no wikilink points to. List all pages, extract every
  `[[target]]`, take the difference:
  `comm -23 <(ls <wiki_root>/**/*.md | sed 's#.*/##;s#\.md$##' | sort -u) <(grep -rho '\[\[[^]]*\]\]' <wiki_root> | sed 's/\[\[//;s/\]\]//;s/|.*//;s/#.*//' | sort -u)`
- **Broken links** — a `[[target]]` whose page file does not exist (invert the diff above).
- **Missing required frontmatter** — per schema §3 (e.g. `created`, `sources`,
  `tags`): `grep -L '^created:' <wiki_root>/**/*.md` (repeat per field).
- **Index/MOC consistency** — pages listed in `index.md` with no file, and
  existing pages absent from any index/MOC (diff index links against the page list).
- **Naming violations** — pages breaking the zone naming rule in schema §2 (e.g.
  not concrete-noun kebab-case): `ls <wiki_root>/notes | grep -vE '^[a-z0-9-]+\.md$'`.

Drop any hit covered by the §7 exemption list before reporting it.

## 2. Semantic checks (LLM judgment)

- **Contradiction pairs (antagonist rule)** — two pages disagree on the same
  fact/judgment. **Name the disagreement** in one sentence (what exactly differs).
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

Write the report to `<wiki_root>/.wiki/lint/YYYYMMDD-HHMM.md` using
`templates/lint-report.md` (create the dir if missing). Then append to
`<wiki_root>/log.md`:
`## [YYYY-MM-DD HH:MM] lint | <scope>: <N issues>`.

## 4. Fix proposals

Per finding, propose a fix and route it through the zone's write policy (schema §2):

- **autonomous** zone → apply the fix directly.
- **propose** zone → present each fix via AskUserQuestion and confirm before writing.

**Lint may apply only:** conflict/stale annotations, broken-link fixes, and index
entry back-fills. Anything else — building a page, merging pages, rewriting
content — is **handed to `/wiki:ingest`** or listed as a human to-do. Never do it here.

**Non-interactive (`claude -p`):** same as ingest — in a propose zone, apply a fix
only if the prompt pre-authorizes it; otherwise the report is the endpoint. Append
the log line regardless.

## Boundary

This skill **diagnoses and annotates**. It does not build knowledge pages or merge
pages (that is `/wiki:ingest`) and does not answer questions (that is `/wiki:query`).
The antagonist rule is absolute: contradictions are named, never smoothed.
