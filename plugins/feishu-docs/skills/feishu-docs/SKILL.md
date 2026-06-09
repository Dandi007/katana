---
name: feishu-docs
description: Pull a Feishu DocX document into the local mirror as a block-tree AST (SSoT) + read-only Markdown pair. Use whenever you need to sync a Feishu doc into the local feishu_docs_root for offline reading, programmatic editing, or knowledge ingestion.
---

# Feishu Docs

Pull a Feishu DocX document into the local mirror. The engine produces two artefacts per document:

- `<feishu_docs_root>/<relPath>/<title>-<docId>.ast.json` — block-tree AST, **SSoT** for programmatic edits
- `<feishu_docs_root>/<relPath>/<title>-<docId>.md` — read-only Markdown render (never edit directly)
- `<feishu_docs_root>/<relPath>/.index.json` — doc-id canonical index (used for dedup / re-pull)

Only `pull` (P0) is implemented. Push/sync come later.

## Build the engine (once)

```bash
cd ${CLAUDE_PLUGIN_ROOT}/engine
bun install
bun build src/cli.ts --target node --outfile dist/cli.js
```

Alternatively, run without building via `bun src/cli.ts` (requires bun on PATH).

## Pull a document

```bash
# Via built dist:
node dist/cli.js pull --doc <feishu-doc-url> [--path <relPath>] [--root <feishu_docs_root>]

# Via bun directly (no build step):
bun src/cli.ts pull --doc <feishu-doc-url> [--path <relPath>] [--root <feishu_docs_root>]
```

| Flag | Required | Default | Notes |
|------|----------|---------|-------|
| `--doc` | yes | — | Full Feishu DocX URL |
| `--path` | no | `.` | Sub-directory within `feishu_docs_root` |
| `--root` | no | value of `feishu_docs_root` in `.katana` | Override mirror root for this call |

## Output layout

```
<feishu_docs_root>/
  <relPath>/
    <title>-<docId>.md          # read-only render
    <title>-<docId>.ast.json    # block-tree AST (SSoT)
    .index.json                 # doc-id → filename map
```

## Credentials

Credentials are managed by **lark-cli** (stored in the OS keychain). They are **never** stored in `.katana` or committed to git. Run `lark-cli auth` if the pull fails with a 401.

The Feishu API call requires `--detail with-ids` internally; the engine handles this automatically.

## Scope

- P0 (implemented): `pull` — one-way Feishu → local
- P1 (planned): `push` / `sync` — local edits back to Feishu
