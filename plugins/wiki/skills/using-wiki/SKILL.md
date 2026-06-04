---
name: using-wiki
description: Hook-injected convention layer for the wiki plugin. Active whenever a wiki is initialized in this project; governs how to ground answers in the wiki, when to route to /wiki:* skills, and the iron rules that keep provenance and linking intact.
---

# Using Wiki

This project has a wiki.

- wiki_root: `{{WIKI_ROOT}}`
- schema: `{{WIKI_ROOT}}/WIKI.md` — read it before any wiki operation.

The wiki is an LLM-compiled, persistently interlinked markdown knowledge base
(Karpathy's "stop re-deriving, start compiling"). It works only if every read is
grounded and every write flows through the governed pipeline. The rules below are
not advice — they are invariants.

## Iron Rules

1. **Orient before answering.** Before answering any knowledge question the wiki may
   cover, orient first via the `/wiki:query` ladder. Never answer bare from parametric
   knowledge. When unsure whether the wiki covers it, a cheap index/grep check settles it.
2. **Cite or stay silent.** Every wiki-grounded answer must carry a **citation** — a
   page link or a source anchor. No citation, no claim. Content not verified against
   the wiki must be explicitly labeled as inference — never assert it with wiki authority.
3. **Write only through `/wiki:ingest`.** Directly `Write`-ing a page is an
   anti-pattern: it bypasses provenance, linking, indexing, and the log. All writes
   go through the ingest pipeline.
4. **Raw is immutable.** Never mutate raw sources. Never ingest your own composite
   output that no human has reviewed — this is the **model-collapse** defense.
5. **Capture durable knowledge.** When durable knowledge surfaces mid-conversation,
   do not interrupt the current task — proactively **offer to capture** it. Durable
   knowledge = facts, decisions, or corrections that stay valid beyond this session.

## Routing

Route by intent, in any language.

| Intent | Skill |
|--------|-------|
| Create a wiki / adopt existing / change schema | `/wiki:init` |
| User wants content saved into the wiki / files in inbox | `/wiki:ingest` |
| A knowledge question | `/wiki:query` |
| Health check / audit / find contradictions | `/wiki:lint` |

## Calling convention

Always call skills fully qualified as `/wiki:<name>`. `/init` is a built-in
Claude Code command — `/wiki:init` must be fully qualified to avoid the collision.
