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
  `grep "^## \[" <wiki_root>/log.md | grep "| lint |"` → last lint timestamp.
  Enumerate pages modified after it. **Default** (the wiki may not be a git
  repo): `find "<wiki_root>" -type d \( -name .wiki -o -name .obsidian -o -name
  .git -o -name .trash \) -prune -o -type f -name '*.md' -newermt "<baseline>"
  -print`. In a git repo you *may* optimize with `git log --since="<baseline>"
  --name-only`. First run ever (no lint line), or baseline unparseable by
  `-newermt` → fall back to **full**.

Ask the user (AskUserQuestion) when scope is unstated. **Non-interactive** →
default **incremental** (full on first run).

## 1. Mechanical checks

Deterministic — run grep/awk ad hoc, do not author a script. Examples use the
placeholder `<wiki_root>`.

**Enumerate pages with `find`, never `ls`/`**/*.md`** (recursive, space/CJK
safe, works on bash 3.2 without globstar). Every page-listing and recursive grep
**must prune interference dirs** so the lint report and Obsidian/git internals
never self-pollute (a sample `[[wikilink]]` inside a past report would otherwise
register as a phantom target). The shared enumerator — reuse it verbatim below:

```
find "<wiki_root>" -type d \( -name .wiki -o -name .obsidian -o -name .git -o -name .trash \) -prune -o -type f -name '*.md' -print
```

For recursive `grep`, pass `--exclude-dir=.wiki --exclude-dir=.obsidian
--exclude-dir=.git --exclude-dir=.trash` (or pipe the `find` list into
`grep -f`). **Orphan/broken-link keying assumes unique basenames across zones**
(the Obsidian convention); if the schema permits duplicate basenames, key on the
relative path instead.

- **Orphan pages** — a page no wikilink points to. List all page basenames,
  extract every `[[target]]` (strip `|alias` and `#anchor`), take the difference.
  Both sides sorted with `LC_ALL=C sort` for a deterministic `comm`:
  `comm -23 <(find "<wiki_root>" -type d \( -name .wiki -o -name .obsidian -o -name .git -o -name .trash \) -prune -o -type f -name '*.md' -print | sed 's#.*/##;s#\.md$##' | LC_ALL=C sort -u) <(find "<wiki_root>" -type d \( -name .wiki -o -name .obsidian -o -name .git -o -name .trash \) -prune -o -type f -name '*.md' -exec grep -ho '\[\[[^]]*\]\]' {} + | sed 's/\[\[//;s/\]\]//;s/|.*//;s/#.*//' | LC_ALL=C sort -u)`
- **Broken links** — a `[[target]]` whose page file does not exist: invert the
  diff above (swap to `comm -13`, same two `LC_ALL=C`-sorted streams).
- **Missing required frontmatter** — per schema §3 (e.g. `created`, `sources`,
  `tags`), iterate the `find` enumerator and `grep -L '^created:'` each page
  (repeat per field).
- **Index/MOC consistency** — pages listed in `index.md` with no file, and
  existing pages absent from any index/MOC (diff index links against the page list).
- **Naming violations** — pages breaking the zone naming rule in schema §2.
  **Iterate every zone row in §2** (not just `notes/`); for each, check **files
  only, one level deep**: `find "<wiki_root>/<zone-path>" -maxdepth 1 -type f
  -name '*.md'`. **Derive the reject pattern from that zone's Naming column** — do
  *not* hardcode `[a-z0-9-]`. Example for an ASCII kebab-case zone:
  `… | sed 's#.*/##' | grep -vE '^[a-z0-9-]+\.md$'`. If §2 declares no naming
  rule for a zone, or the rule is CJK/free-form (no machine-checkable pattern),
  **skip this check for that zone and note it in the report's Skipped section**.

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

Write the report to `<wiki_root>/.wiki/lint/YYYYMMDD-HHMM.md` using
`templates/lint-report.md` (create the dir if missing). Every report section has
an empty state — write `— none —` when a section has no findings. Then append to
`<wiki_root>/log.md`:
`## [YYYY-MM-DD HH:MM] lint | <scope>: <N issues>`. A clean run is still logged —
N=0 → `## [YYYY-MM-DD HH:MM] lint | full: 0 issues — clean`.

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
