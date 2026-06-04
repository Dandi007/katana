---
name: query
description: Answer any knowledge question the wiki may cover, in any language. The read path for the wiki — orient, read candidate pages, synthesize a cited answer, and judge whether the synthesis should be backfilled. Triggered by iron rule 1 (orient before answering); never answer bare from parametric knowledge.
---

# Query

The read path. toolboxmd's karpathy-wiki once lost its read protocol in a refactor —
the agent answered bare from parametric knowledge and the wiki stopped mattering.
The protocol was restored and made an iron rule. This ladder is **deterministic with
hard thresholds** so that any model, including a weak one, walks the same path —
not by judgment, by rule.

`wiki_root` is injected by the using-wiki hook. Run `date "+%Y-%m-%d %H:%M"`
before any timestamped step (the gap log) — use that real time.

Walk the five rungs in order. Do not skip a rung.

## 1. Orient

- Read `<wiki_root>/WIKI.md` **§6** (citation format, back-fill rule) — it is the contract.
- Read the index / relevant MOC. If no index/MOC exists (common in adopted libraries),
  proceed directly to grep.
- Grep key terms, expanding each to 2–4 synonyms or language variants derived from
  the question and any schema glossary (a term miss is a coverage miss).
- Produce a **candidate page list** — one line each: `<page path>` + one clause on
  why it's relevant. An empty list is a valid outcome → go to rung 4.

## 2. Read

The hard threshold is **5**.

- Candidates **≤5** → read all of them inline. No skipping ("close enough").
- Candidates **>5** → dispatch an **Explore subagent**: give it the question and the
  full candidate list, require it to return the relevant passages with their page
  paths. Read the converged set inline after it reports. If no subagent tool is
  available, read up to 10 inline (most relevant first) and state the coverage
  limitation in the answer.

Never inline-read a >5 set (context waste) and never read only the first few of a
large set (coverage gap). The threshold decides, not your gut.

## 3. Synthesize with citations

- Compose the answer from what you read.
- **Every claim carries a citation** — a wikilink to the page, or a `source` anchor
  for raw-backed claims, formatted per schema **§6**.
- Bridging prose the wiki does not directly support — anything you reason in to
  connect pages — must be **explicitly labeled** `[inference]` (or equivalent).
- A sentence with neither a citation nor an `[inference]` label must not appear in a
  wiki-grounded answer. That is the contract.

## 4. Cold path

Candidate set empty, or everything read turned out irrelevant:

- State plainly: **"the wiki does not cover this."** Do **not** quietly fall back to
  parametric knowledge and dress it up as a wiki answer.
- **Interactive:** ask the user whether to web-search or to answer from general
  knowledge — and if you do, label it clearly as **non-wiki**.
- **Always** record the gap: run `date "+%Y-%m-%d %H:%M"` first, then append to
  `<wiki_root>/log.md`:
  `## [YYYY-MM-DD HH:MM] query | gap: <question>` (the only write this skill performs).
  Then propose adding the topic to the ingest backlog.

## 5. Backfill judgment

Apply schema **§6**: if synthesizing **≥2 pages** produced a structure not present
in any single page (a comparison table or a conclusion), proactively offer to write
it back via `/wiki:ingest` (conversation-capture shape). Backfill only on the user's
yes — this skill writes nothing itself (the gap log line excepted).

## Forbidden

- Skipping a rung.
- A >5 candidate set read inline (context waste) **or** only its first few read
  (coverage gap) — the threshold of 5 is not negotiable.
- Bridging from training knowledge without an `[inference]` label.
- A cold result disguised as a wiki-grounded answer.

## Non-interactive (`claude -p`)

AskUserQuestion is unavailable, so the cold path does not wait. Output
"the wiki does not cover this", write the gap log line, and — only if the prompt
permits — give a general-knowledge answer **explicitly labeled non-wiki**. Never block.

## Answer format

```
<answer, each claim cited or [inference]-labeled>

The two mechanisms share a common origin [inference] — neither page states this
directly, but both trace to the same foundational constraint.

Sources:
- [[page-a]]
- [[page-b]] — anchor / source ref
- raw/article.md#L12-L40
```

## Boundary

This skill **reads only** — the single exception is the gap log line on the cold
path. Backfill goes through `/wiki:ingest`; finding contradictions across pages is
`/wiki:lint`'s job. Citation format details live in schema §6.
