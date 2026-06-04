---
name: ingest
description: The wiki's single write pipeline. Use whenever content should be saved into the wiki — importing a source (file/URL), capturing a durable insight from this conversation, or processing the inbox. Triggers on any "put X into the wiki / 把 X 入库 / 收录这个" intent, in any language. This is the only governed path that writes pages; direct Write is an anti-pattern.
---

# Ingest

The single write pipeline. Every page, link, index entry, and log line the wiki
gains is created here — never by a bare `Write`. This pipeline is where the four
failure modes of AI-maintained knowledge (mediocrity collapse, model collapse,
cognitive offloading, container-boundary erosion) are held back: provenance,
mandatory linking, zone write policy, and the resist-table all live in these steps.

`wiki_root` is injected by the using-wiki hook. Run `date "+%Y-%m-%d %H:%M"`
before any timestamped step (raw archival, log, commit) — use that real time.

## Input — four shapes

Identify which one you have before starting:

1. **Inbox file** — a file already sitting in the wiki's inbox dir.
2. **Explicit path** — the user names a local file to ingest.
3. **URL** — a web source to fetch.
4. **Conversation capture** — "put what we just discussed about X into the wiki."
   The source is this conversation; no external file exists yet.

Then run the eight steps in order.

## 1. Read schema

Read `<wiki_root>/WIKI.md` — it is the contract, not optional.
- Determine the target **zone** from **§2** (path, purpose, naming).
- Pull that zone's **write policy**, **page template**, and **naming rule** (§2).
- Internalize **§3 Page Conventions**: required frontmatter, the provenance rule
  (every claim traces to a listed source), and the link rules — including that
  **referenced pages get back-updated with a reciprocal link**.
- Read **§4 Create-vs-Update Criteria** and **§5 Ingest Specifics** — §5 declares
  this library's source types and any special handling (raw path, etc.).
Do not assume defaults; the schema overrides this skill where they differ.

## 2. Read source

- **Local file / inbox:** read it directly.
- **URL:** fetch and save a copy into the raw layer first (the path declared in
  schema §5; if none, `<wiki_root>/raw/`), then read the **saved copy**. This keeps
  raw immutable and provenance linkable — never ingest straight from a live URL.
- **Conversation capture:** the relevant turns are the source; note them for §8 provenance.

## 3. Orient

Find the candidate set of existing pages this content touches:
- Read the index / relevant MOC.
- `grep` the wiki for the content's key terms **and their synonyms**.
List each candidate with its relation to the new content: **will update** /
**will link** / **unrelated**. This is the comparison pass that prevents duplicate pages.

## 4. Judge units

Split the source into discrete **knowledge units**. Run each unit through the
create-vs-update criteria — **read `references/create-vs-update.md`** and apply it
unit by unit (update first; create only when all three conditions hold; record
merge candidates and skips). Do not eyeball it.

## 5. Build the proposal package

Assemble the full package before touching disk:
- **New-page drafts** — each following the zone's page template + required
  frontmatter (§3), `sources:` populated per `references/provenance.md`.
- **Existing-page diffs** — including the **reciprocal back-links** on every page a
  new page references (schema §3 rule — do this, don't skip it).
- **Link plan** — every new page carries **≥1 outlink** (no islands).
- **Index / MOC diff** — new pages registered so query can reach them.

## 6. Resist-table self-check

**Read `references/resist-table.md`** and walk every row against your proposal. If
you catch yourself leaning on any excuse in that table, revise the package before
proceeding. This step is mandatory, not advisory.

## 7. Apply per zone write policy

- **autonomous** zone → write directly (proceed to §8).
- **propose** zone → present the full proposal package and confirm each item via
  AskUserQuestion before writing.
- **Non-interactive (`claude -p`):** AskUserQuestion is unavailable. In a propose
  zone, write **only** if the prompt explicitly pre-authorizes it (e.g. "本次提案视为已批准"
  / "proposals pre-approved"). Otherwise do not write any page — output the full
  proposal text and append one log line `ingest-proposed`, then stop.

## 8. Write + record

- Write all approved files (new pages, updated pages, index/MOC).
- Append to `<wiki_root>/log.md`:
  `## [YYYY-MM-DD HH:MM] ingest | <source>` followed by body lines listing every
  **created** and **updated** page.
- `git commit` with message `wiki: ingest <source>` — unless the schema declares
  commits off.

## Boundary

This skill **writes**. It does not answer questions (that's `/wiki:query`) and
does not run a whole-wiki health check (that's `/wiki:lint`). Provenance format
details live in `references/provenance.md`.
