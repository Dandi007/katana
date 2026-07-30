"""katana-wiki-v2-mcp — v2 wiki FastMCP server.

Tools:
  wiki_get, wiki_read, wiki_list — read
  wiki_search, wiki_query, wiki_report_gap — search
  wiki_create, wiki_update, wiki_edit, wiki_rename, wiki_delete — write (single-gate)
  wiki_ingest_plan, wiki_ingest_apply — batch ingest
  fs_read, fs_list, fs_glob, fs_stat — read-only VFS
  wiki_meta_write — meta file write

CLI:
  katana-wiki-v2-mcp                  — start MCP server
  katana-wiki-v2-mcp --rebuild-index  — rebuild search index from pages/
"""
import argparse
import datetime
import json
import os
import sys
from fastmcp import FastMCP

from katana_wiki_v2_mcp import pages as _pages
from katana_wiki_v2_mcp import query as _query
from katana_wiki_v2_mcp import search as _search
from katana_wiki_v2_mcp import vfs as _vfs
from katana_wiki_v2_mcp.store import WikiStore

mcp = FastMCP(
    "katana-wiki-v2-mcp",
    instructions=(
        "Wiki v2 MCP 接口：平铺页面、稳定 ID、单门写入、内嵌 hybrid 检索。"
        "wiki_get/wiki_read/wiki_list 做发现与读取；"
        "wiki_search/wiki_query 做进程内检索；"
        "wiki_create/wiki_update/wiki_edit/wiki_rename/wiki_delete 做受治理的 mutation；"
        "wiki_ingest_plan/wiki_ingest_apply 做批量入库；"
        "fs_read/fs_list/fs_glob/fs_stat 做只读 VFS；"
        "wiki_meta_write 做元文件写入。"
    ),
)

_store: WikiStore | None = None


def configure(data_root: str, embedding_base_url: str = "http://172.22.62.133:18081",
              embedding_api_key_path: str = "", embedding_model: str = "BAAI/bge-small-zh-v1.5",
              embedding_dim: int = 512) -> None:
    global _store
    embedding_client = _search.EmbeddingClient(
        base_url=embedding_base_url,
        api_key_path=embedding_api_key_path,
        model=embedding_model,
        dim=embedding_dim,
    )
    _store = WikiStore(data_root, embedding_client=embedding_client)


def configure_with_embedding_client(data_root: str, embedding_client) -> None:
    global _store
    _store = WikiStore(data_root, embedding_client=embedding_client)


def _get_store() -> WikiStore:
    if _store is None:
        raise RuntimeError("wiki v2 store not initialized; call configure() first")
    return _store


# ── read tools ──────────────────────────────────────────────────────────────

@mcp.tool()
async def wiki_get(ref: str) -> dict:
    return _get_store().wiki_get(ref)


@mcp.tool()
async def wiki_read(ref: str, offset: int | None = None, limit: int | None = None) -> dict:
    return _get_store().wiki_read(ref, offset=offset, limit=limit)


@mcp.tool()
async def wiki_list(prefix: str | None = None, limit: int = 50, cursor: str | None = None) -> dict:
    return _get_store().wiki_list(prefix=prefix, limit=limit, cursor=cursor)


# ── search tools ────────────────────────────────────────────────────────────

@mcp.tool()
async def wiki_search(query: str, top_k: int = 10) -> dict:
    return _get_store().wiki_search(query, top_k=top_k)


@mcp.tool()
async def wiki_query(question: str, top_k: int = 10) -> dict:
    return _get_store().wiki_query(question, top_k=top_k)


@mcp.tool()
async def wiki_report_gap(question: str, note: str | None = None) -> dict:
    return _get_store().wiki_report_gap(question, note=note)


# ── write tools ─────────────────────────────────────────────────────────────

@mcp.tool()
async def wiki_create(title: str, body: str, frontmatter: dict,
                      allow_no_outlink: bool = False) -> dict:
    return _get_store().wiki_create(title, body, frontmatter,
                                    allow_no_outlink=allow_no_outlink)


@mcp.tool()
async def wiki_update(ref: str, body: str, frontmatter: dict | None = None) -> dict:
    return _get_store().wiki_update(ref, body, frontmatter=frontmatter)


@mcp.tool()
async def wiki_edit(ref: str, old_string: str, new_string: str) -> dict:
    return _get_store().wiki_edit(ref, old_string, new_string)


@mcp.tool()
async def wiki_rename(ref: str, new_title: str) -> dict:
    return _get_store().wiki_rename(ref, new_title)


@mcp.tool()
async def wiki_delete(ref: str, force: bool = False,
                      inlink_action: str | None = None) -> dict:
    return _get_store().wiki_delete(ref, force=force, inlink_action=inlink_action)


@mcp.tool()
async def wiki_ingest_plan(sources: str) -> dict:
    return _get_store().wiki_ingest_plan(sources)


@mcp.tool()
async def wiki_ingest_apply(plan: dict) -> dict:
    return _get_store().wiki_ingest_apply(plan)


# ── meta tool ───────────────────────────────────────────────────────────────

@mcp.tool()
async def wiki_meta_write(name: str, content: str) -> dict:
    return _get_store().wiki_meta_write(name, content)


# ── VFS tools (read-only) ───────────────────────────────────────────────────

@mcp.tool()
async def fs_read(path: str, offset: int | None = None,
                  limit: int | None = None) -> dict:
    return _vfs.fs_read(_get_store()._data_root, path, offset=offset, limit=limit)


@mcp.tool()
async def fs_list(path: str = "") -> dict:
    return _vfs.fs_list(_get_store()._data_root, path)


@mcp.tool()
async def fs_glob(pattern: str) -> dict:
    return _vfs.fs_glob(_get_store()._data_root, pattern)


@mcp.tool()
async def fs_stat(path: str) -> dict:
    return _vfs.fs_stat(_get_store()._data_root, path)


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="katana-wiki-v2-mcp server")
    parser.add_argument("--rebuild-index", action="store_true",
                        help="Rebuild the search index from pages/ and exit")
    args = parser.parse_args()

    data_root = os.environ.get("KATANA_WIKI_V2_ROOT", ".")
    embedding_base_url = os.environ.get("KATANA_WIKI_V2_EMBEDDING_URL", "http://172.22.62.133:18081")
    embedding_api_key_path = os.environ.get("KATANA_WIKI_V2_EMBEDDING_KEY_PATH", "")
    embedding_model = os.environ.get("KATANA_WIKI_V2_EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    embedding_dim = int(os.environ.get("KATANA_WIKI_V2_EMBEDDING_DIM", "512"))

    configure(data_root, embedding_base_url, embedding_api_key_path, embedding_model, embedding_dim)

    if args.rebuild_index:
        store = _get_store()
        result = store.rebuild_index()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(0)

    host = os.environ.get("KATANA_WIKI_V2_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WIKI_V2_MCP_PORT", "5602"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()