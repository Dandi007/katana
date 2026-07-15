---
name: init
description: Create, adopt, or evolve a wiki's schema (WIKI.md). Use when the user wants to start a wiki here, turn an existing notes folder into a governed wiki, or change an existing wiki's zones / write policy / page conventions. The schema is the product — this skill writes WIKI.md and scaffolding only, never knowledge pages.
---

# Init

`WIKI.md` is the product. This skill creates and edits it. It carries operational
discipline; all library-specific rules go *into* the schema you write.

Always run `date "+%Y-%m-%d %H:%M"` before touching the log — use that real timestamp.

## Mode selection (check in order)

1. `.katana` has no `wiki_root`, **or** wiki MCP `fs_glob` shows no `WIKI.md` and an empty/near-empty target (≤3 markdown files, excluding `CLAUDE.md`, `README*`, and template files) → **Bootstrap**.
2. Target dir has existing content but no `WIKI.md` → **Adopt**.
3. `WIKI.md` already exists → **Evolve**.

## Bootstrap — new library

1. Ask the user (AskUserQuestion): purpose, zones, write policy per zone, and optionally an embedding endpoint (default: none).
2. Instantiate `templates/schema.md`, then use wiki MCP `fs_write(path="WIKI.md")`, filling every `{{placeholder}}` from the answers; keep the written defaults (single `notes/` zone, atomic-card model) where the user gives nothing.
3. Scaffold with `fs_create`/`fs_write`: `index.md` (entry MOC), `log.md` (append-only journal), `inbox/` (ingest landing).
4. Write `wiki_root` into client-local `.katana` with the native file tool — **append** the key, never overwrite other keys. If `wiki_root` already exists in `.katana`, do not append a duplicate — confirm with the user before changing it.

Persist WIKI.md and scaffolding through wiki MCP first, update client-local `.katana` last; safe to re-run after interruption (existing files are kept, only missing pieces are created).

**Non-interactive (`claude -p`):** if AskUserQuestion is unavailable, build the schema from parameters given in the prompt plus template defaults. Do not block waiting for input.

## Adopt — existing notes folder

1. Scan logical paths with `fs_list`/`fs_glob`; use `fs_read` for `CLAUDE.md`, templates, and a sample of **min(10, all) notes** — if fewer than 10, read all (frontmatter + naming habits).
2. **Induce** zones and page conventions *from what's already there* — do not impose the default template.
3. Present the induced proposal; the human confirms or edits.
4. Generate `WIKI.md` with `fs_write`. Scaffold only what's missing via `fs_create` — if the library already has a MOC/Index system, the schema **declares reuse** of it instead of creating `index.md`.
5. Write `wiki_root` to client-local `.katana` with the native file tool (append); if `wiki_root` already exists in `.katana`, do not append a duplicate — confirm with the user before changing it. Append the wiki log line with `fs_edit`.

Persist WIKI.md and scaffolding through wiki MCP first, update client-local `.katana` last; safe to re-run after interruption (existing files are kept, only missing pieces are created).

## Evolve — existing wiki

1. Read the current `WIKI.md` with wiki MCP `fs_read`.
2. Discuss the revision with the user.
3. Apply the edit with `fs_edit` (or `fs_write` for a full replacement).
4. If `log.md` doesn't exist, create it with `fs_create`. Append with `fs_edit`: `## [YYYY-MM-DD HH:MM] init | schema updated: <summary>`.

## log.md convention

Append-only journal. Every entry header:

```
## [YYYY-MM-DD HH:MM] <op> | <subject>
```

`<op>` ∈ `ingest` \| `query` \| `lint` \| `init`. Headers are grep-parseable via
The server keeps these headers queryable. Body lines (details, links) follow under the header.

## Boundary

This skill writes **WIKI.md and scaffolding only**. It never writes knowledge
pages — that is `/wiki:ingest`'s job. If the user wants content captured during
init, hand off to `/wiki:ingest` after the schema exists.
