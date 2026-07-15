---
name: init
description: Create, adopt, or evolve a wiki's schema (WIKI.md). Use when the user wants to start a wiki here, turn an existing notes folder into a governed wiki, or change an existing wiki's zones / write policy / page conventions. The schema is the product — this skill writes WIKI.md and scaffolding only, never knowledge pages.
---

# Init

`WIKI.md` is the product. This skill creates and edits it. It carries operational
discipline; all library-specific rules go *into* the schema you write.

Always run `date "+%Y-%m-%d %H:%M"` before touching the log — use that real timestamp.

## Mode selection (check in order)

Use wiki MCP `fs_glob`, `fs_list` and `fs_read` to inspect wiki content. `.katana`
is client-local configuration and remains the only file accessed with native client tools.

1. `.katana` has no `wiki_root`, **or** wiki MCP reports no `WIKI.md` and an empty/near-empty library (≤3 markdown files, excluding `CLAUDE.md`, `README*`, and template files) → **Bootstrap**.
2. Wiki MCP reports existing content but no `WIKI.md` → **Adopt**.
3. `WIKI.md` exists according to wiki MCP → **Evolve**.

## Bootstrap — new library

1. Ask the user (AskUserQuestion): purpose, zones, write policy per zone, and optionally an embedding endpoint (default: none).
2. Instantiate the skill-local `templates/schema.md`, then create `WIKI.md` with wiki MCP `fs_create` / `fs_write`, filling every placeholder; keep the written defaults (single `notes/` zone, atomic-card model) where the user gives nothing.
3. Scaffold `index.md`, `log.md`, and `inbox/` through wiki MCP `fs_create` / `fs_write`.
4. Write `wiki_root` into `.katana` — **append** the key, never overwrite other keys. If `wiki_root` already exists in `.katana`, do not append a duplicate — confirm with the user before changing it.

Write wiki content through MCP first, update client-local `.katana` last; safe to re-run after interruption (existing files are kept, only missing pieces are created).

**Non-interactive (`claude -p`):** if AskUserQuestion is unavailable, build the schema from parameters given in the prompt plus template defaults. Do not block waiting for input.

## Adopt — existing notes folder

1. Use `fs_list` / `fs_glob` for structure and `fs_read` for existing conventions from `CLAUDE.md`, any template dir, and a sample of **min(10, all) notes** — if fewer than 10, read all (frontmatter + naming habits).
2. **Induce** zones and page conventions *from what's already there* — do not impose the default template.
3. Present the induced proposal; the human confirms or edits.
4. Generate `WIKI.md` with `fs_write` from the confirmed proposal. Scaffold only what's missing with `fs_create` / `fs_write` — if the library already has a MOC/Index system, the schema **declares reuse** of it instead of creating `index.md`.
5. Write `wiki_root` to `.katana` (append); if `wiki_root` already exists in `.katana`, do not append a duplicate — confirm with the user before changing it. Append a log line.

Write wiki content through MCP first, update client-local `.katana` last; safe to re-run after interruption.

## Evolve — existing wiki

1. Read the current `WIKI.md` with wiki MCP `fs_read`.
2. Discuss the revision with the user.
3. Apply the edit with wiki MCP `fs_edit`.
4. If `log.md` doesn't exist, create it with `fs_create` / `fs_write`. Append with `fs_edit`: `## [YYYY-MM-DD HH:MM] init | schema updated: <summary>`.

## log.md convention

Append-only journal. Every entry header:

```
## [YYYY-MM-DD HH:MM] <op> | <subject>
```

`<op>` ∈ `ingest` \| `query` \| `lint` \| `init`. Headers are grep-parseable via
Body lines (details, links) follow under the header; journal parsing belongs to the MCP server.

## Boundary

This skill writes **WIKI.md and scaffolding only**. It never writes knowledge
pages — that is `/wiki:ingest`'s job. If the user wants content captured during
init, hand off to `/wiki:ingest` after the schema exists.
