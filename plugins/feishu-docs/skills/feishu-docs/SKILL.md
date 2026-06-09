---
name: feishu-docs
description: Pull a Feishu DocX document into the local mirror as a block-tree AST (SSoT) + read-only Markdown pair. Use whenever you need to sync a Feishu doc into the local feishu_docs_root for offline reading, programmatic editing, or knowledge ingestion.
---

# Feishu Docs

Pull a Feishu DocX document into the local mirror. The engine produces two artefacts per document, stored **flat** under `feishu_docs_root`:

- `<feishu_docs_root>/<title>-<docId>.ast.json` — block-tree AST, **SSoT** for programmatic edits
- `<feishu_docs_root>/<title>-<docId>.md` — read-only Markdown render (never edit directly)
- `<feishu_docs_root>/.index.json` — doc-id canonical index (used for dedup / re-pull)

Only `pull` (P0) is implemented. Push/sync come later.

## Layout is flat — Feishu hierarchy lives in frontmatter, NOT folders

Files are **flat** in `feishu_docs_root`. **Do not create sub-directories per document.** The filename is self-identifying (`<title>-<docId>`), so flatness is collision-free and re-pull is keyed by `docId`, not path. This also matches the Zettelkasten host paradigm (structure via links/metadata, not folders).

The document's location in Feishu is preserved as **frontmatter** in the `.md` (and `location` in the AST):

```yaml
---
title: "Mock 仿真测试体系设计文档"
feishu_doc_id: "C79cd…"
feishu_url: "https://…/wiki/TYAuw…"
feishu_obj_type: "docx"
feishu_space_id: "7595126051555003334"
feishu_breadcrumb: "工程基建 / Edge Infra"        # 祖先链（wiki 文档）
feishu_parent: "[[Edge Infra-LonZd…]]"          # 父文档 wikilink → 层级即链接图
---
```

- **wiki** documents carry a full ancestor `feishu_breadcrumb` + a `feishu_parent` wikilink (the Feishu tree becomes a navigable link graph locally).
- **drive** documents (e.g. minutes) carry only `feishu_url` / `feishu_obj_type` — Feishu has no API to resolve a drive doc's parent folder, so they have no breadcrumb. This is expected, not an error.

There is **no `--path` flag**. The engine derives location automatically; you never choose a sub-directory.

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
node dist/cli.js pull --doc <feishu-doc-url> [--root <feishu_docs_root>]

# Via bun directly (no build step):
bun src/cli.ts pull --doc <feishu-doc-url> [--root <feishu_docs_root>]
```

| Flag | Required | Default | Notes |
|------|----------|---------|-------|
| `--doc` | yes | — | Full Feishu DocX or Wiki URL (wiki URLs auto-resolve to the underlying docx) |
| `--root` | no | value of `feishu_docs_root` in `.katana`, else `docs/feishu` | Mirror root |

The command prints a JSON result. For a non-docx node (a wiki `file`/`sheet`/`bitable`/`mindnote`), it returns `{"skipped":true,"reason":…}` and writes nothing — only `docx` is supported.

## Credentials

Credentials are managed by **lark-cli** (stored in the OS keychain). They are **never** stored in `.katana` or committed to git. Run `lark-cli auth` if the pull fails with a 401.

The Feishu API calls (`docs +fetch --detail with-ids` for content, `wiki +node-get` for location) are handled internally by the engine.

## Scope

- P0 (implemented): `pull` — one-way Feishu → local (flat + frontmatter link graph)
- P1 (planned): `push` / `sync` — local edits back to Feishu
