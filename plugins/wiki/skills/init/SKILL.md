---
name: init
description: Create, adopt, or evolve a wiki's schema (WIKI.md). Use when the user wants to start a wiki here, turn an existing notes folder into a governed wiki, or change an existing wiki's zones / write policy / page conventions. The schema is the product — this skill writes WIKI.md and scaffolding only, never knowledge pages.
---

# Init

`WIKI.md` is the product. This skill creates and edits it. It carries operational
discipline; all library-specific rules go *into* the schema you write.

Always run `date "+%Y-%m-%d %H:%M"` before touching the log — use that real timestamp.

## Mode selection (check in order)

1. `.katana` has no `wiki_root`, **or** the target dir has no `WIKI.md` and is empty/near-empty → **Bootstrap**.
2. Target dir has existing content but no `WIKI.md` → **Adopt**.
3. `WIKI.md` already exists → **Evolve**.

## Bootstrap — new library

1. Ask the user (AskUserQuestion): purpose, zones, and write policy per zone.
2. Instantiate `templates/schema.md` into `<wiki_root>/WIKI.md`, filling every `{{placeholder}}` from the answers; keep the written defaults (single `notes/` zone, atomic-card model) where the user gives nothing.
3. Scaffold: `index.md` (entry MOC), `log.md` (append-only journal), `inbox/` (ingest landing).
4. Write `wiki_root` into `.katana` — **append** the key, never overwrite other keys.

**Non-interactive (`claude -p`):** if AskUserQuestion is unavailable, build the schema from parameters given in the prompt plus template defaults. Do not block waiting for input.

## Adopt — existing notes folder

1. Scan the dir: structure, plus existing conventions from `CLAUDE.md`, any template dir, and a sample of **≥10 notes** (frontmatter + naming habits).
2. **Induce** zones and page conventions *from what's already there* — do not impose the default template.
3. Present the induced proposal; the human confirms or edits.
4. Generate `WIKI.md` from the confirmed proposal. Scaffold only what's missing — if the library already has a MOC/Index system, the schema **declares reuse** of it instead of creating `index.md`.
5. Write `wiki_root` to `.katana` (append) and append a log line.

## Evolve — existing wiki

1. Read the current `WIKI.md`.
2. Discuss the revision with the user.
3. Apply the edit.
4. Append to `log.md`: `## [YYYY-MM-DD HH:MM] init | schema updated: <summary>`.

## log.md convention

Append-only journal. Every entry header:

```
## [YYYY-MM-DD HH:MM] <op> | <subject>
```

`<op>` ∈ `ingest` \| `query` \| `lint` \| `init`. Headers are grep-parseable via
`grep "^## \[" log.md`. Body lines (details, links) follow under the header.

## Boundary

This skill writes **WIKI.md and scaffolding only**. It never writes knowledge
pages — that is `/wiki:ingest`'s job. If the user wants content captured during
init, hand off to `/wiki:ingest` after the schema exists.
