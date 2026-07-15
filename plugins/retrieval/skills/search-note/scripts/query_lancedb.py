#!/usr/bin/env python3
"""Query the derived search-note index.

The script prefers the JSONL cache produced under the canonical LanceDB cache
path. When that cache is missing, it can scan Markdown files directly as a
keyword fallback. Output is JSON with the top_k result schema used by SKILL.md.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


CACHE_BASE = Path.home() / ".cache" / "agent-knowledge" / "Zettelkasten"
MANIFEST_PATH = CACHE_BASE / "index-manifest.json"
OPCODE_MANIFEST_PATH = CACHE_BASE / "opencode-index-manifest.json"
LANCEDB_PATH = CACHE_BASE / "lancedb"
CHUNKS_JSONL = LANCEDB_PATH / "chunks.jsonl"
OPCODE_TABLE = "opencode_sessions"
DEFAULT_OPCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_ZHIPU_EMBEDDING_MODEL = "embedding-3"
DEFAULT_HTTP_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_HTTP_EMBEDDING_ENDPOINT = "http://172.22.62.133:18081/v1/embeddings"
DEFAULT_HTTP_EMBEDDING_API_KEY_ENV = "EMBEDDING_API_KEY"
DEFAULT_HTTP_EMBEDDING_API_KEY_FILE = Path.home() / ".config" / "agent-knowledge" / "Zettelkasten" / "embedding_api_key"
DEFAULT_HTTP_DIMENSIONS = 512
DEFAULT_ZHIPU_DIMENSIONS = 1024
WORD_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SECRET_REPLACEMENTS = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s'\"]+"), r"\1<redacted>"),
    (re.compile(r"\b[0-9a-fA-F]{32}\.[A-Za-z0-9_-]{10,}\b"), "<redacted-api-key>"),
    (re.compile(r"(?i)([A-Za-z_][A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET)[A-Za-z0-9_]*\s*=\s*)[^\s'\"]+"), r"\1<redacted>"),
]
MIGRATED_MARKDOWN_SCOPES = (
    "Zettelkasten",
    "DeepThought",
    "转换文档",
    "inbox",
    "智元工作/工作记录",
    ".wiki",
    "WIKI.md",
)


def is_migrated_markdown_path(relative_path: str) -> bool:
    normalized = relative_path.strip("/")
    return any(
        normalized == scope or normalized.startswith(scope.rstrip("/") + "/")
        for scope in MIGRATED_MARKDOWN_SCOPES
    )


def in_requested_scope(relative_path: str, scope: list[str] | None) -> bool:
    if is_migrated_markdown_path(relative_path):
        return False
    if not scope:
        return True
    return any(
        relative_path.startswith(item.rstrip("/") + "/") or relative_path == item.rstrip("/")
        for item in scope
    )


def terms_for(query: str) -> list[str]:
    return [term.lower() for term in WORD_RE.findall(query) if term.strip()]


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_records(chunks_jsonl: Path) -> list[dict[str, Any]]:
    if not chunks_jsonl.exists():
        return []
    records: list[dict[str, Any]] = []
    with chunks_jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def optional_vector_backend() -> tuple[Any | None, Any | None, str | None]:
    try:
        import lancedb  # type: ignore
    except Exception as exc:  # pragma: no cover - optional deps
        return None, None, f"missing LanceDB dependency: {exc.__class__.__name__}"
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as exc:  # pragma: no cover - optional deps
        return lancedb, None, f"missing local embedding dependency: {exc.__class__.__name__}"
    return lancedb, SentenceTransformer, None


def validate_env_name(name: str) -> None:
    if not ENV_NAME_RE.match(name):
        raise RuntimeError("Invalid Zhipu API key environment variable name")


def safe_exception_summary(error: Exception) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTPError code={error.code}"
    if isinstance(error, RuntimeError):
        message = str(error)
        safe_messages = {
            "Configured Zhipu API key environment variable is unset",
            "Configured HTTP embedding API key is unset",
            "local semantic query requires sentence-transformers",
        }
        if message in safe_messages:
            return f"RuntimeError: {message}"
    return error.__class__.__name__


def http_api_key(api_key_env: str | None, api_key_file: str | None) -> str:
    if api_key_env:
        validate_env_name(api_key_env)
        api_key = os.environ.get(api_key_env, "").strip()
        if api_key:
            return api_key
    if api_key_file:
        path = Path(api_key_file).expanduser()
        if path.exists():
            api_key = path.read_text(encoding="utf-8").strip()
            if api_key:
                return api_key
    raise RuntimeError("Configured HTTP embedding API key is unset")


def redact_text(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_REPLACEMENTS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def zhipu_query_embedding(query: str, *, api_key_env: str, model: str, dimensions: int, timeout: int = 60) -> list[float]:
    validate_env_name(api_key_env)
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        raise RuntimeError("Configured Zhipu API key environment variable is unset")
    body = json.dumps({"model": model, "input": query, "dimensions": dimensions}).encode("utf-8")
    request = urllib.request.Request(
        "https://open.bigmodel.cn/api/paas/v4/embeddings",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Zhipu query embedding request failed: HTTPError code={error.code}") from None
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if len(rows) != 1:
        raise RuntimeError(f"Zhipu query embedding response count mismatch: expected 1, got {len(rows)}")
    return list(rows[0]["embedding"])


def http_query_embedding(query: str, *, endpoint: str, api_key_env: str | None, api_key_file: str | None = str(DEFAULT_HTTP_EMBEDDING_API_KEY_FILE), model: str, timeout: int = 60) -> list[float]:
    headers = {"Content-Type": "application/json"}
    if api_key_env or api_key_file:
        api_key = http_api_key(api_key_env, api_key_file)
        headers["Authorization"] = f"Bearer {api_key}"
    body = json.dumps({"model": model, "input": [query], "is_query": True}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP query embedding request failed: HTTPError code={error.code}") from None
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    if len(rows) != 1:
        raise RuntimeError(f"HTTP query embedding response count mismatch: expected 1, got {len(rows)}")
    return list(rows[0]["embedding"])


def lancedb_table_names(db: Any) -> list[str]:
    response = db.list_tables() if hasattr(db, "list_tables") else db.table_names()
    tables = getattr(response, "tables", response)
    return list(tables or [])


def snippet_for(text: str, terms: list[str], size: int = 220) -> str:
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms if term and lowered.find(term) >= 0]
    start = max(min(positions) - 60, 0) if positions else 0
    snippet = text[start : start + size].replace("\n", " ").strip()
    if start > 0:
        snippet = "…" + snippet
    if start + size < len(text):
        snippet += "…"
    return redact_text(snippet)


def score_record(record: dict[str, Any], query: str, terms: list[str]) -> tuple[float, list[str]]:
    title = str(record.get("title", ""))
    rel = str(record.get("relative_path", ""))
    heading = str(record.get("heading_path", ""))
    text = str(record.get("chunk_text", ""))
    haystacks = {
        "title": title.lower(),
        "path": rel.lower(),
        "heading": heading.lower(),
        "text": text.lower(),
        "wikilinks": " ".join(record.get("wikilinks") or []).lower(),
        "tags": " ".join(record.get("tags") or []).lower(),
    }
    query_l = query.lower()
    score = 0.0
    match_types: set[str] = set()
    if query_l and query_l in haystacks["title"]:
        score += 8.0
        match_types.add("title")
    if query_l and query_l in haystacks["path"]:
        score += 4.0
        match_types.add("title")
    for term in terms:
        if term in haystacks["title"]:
            score += 3.0
            match_types.add("title")
        if term in haystacks["heading"]:
            score += 2.0
            match_types.add("keyword")
        text_count = haystacks["text"].count(term)
        if text_count:
            score += min(5.0, 1.0 + math.log1p(text_count))
            match_types.add("keyword")
        if term in haystacks["wikilinks"]:
            score += 2.5
            match_types.add("wikilink")
        if term in haystacks["tags"]:
            score += 1.5
            match_types.add("keyword")
    if not match_types and query_l and query_l in haystacks["text"]:
        score += 1.0
        match_types.add("keyword")
    return score, sorted(match_types)


def semantic_query(
    *,
    lancedb_module: Any,
    sentence_transformers_cls: Any,
    lancedb_path: Path,
    query: str,
    top_k: int,
    scope: list[str] | None,
    embedding_model: str,
    embedding_backend: str,
    dimensions: int,
    api_key_env: str,
    embedding_endpoint: str,
    embedding_api_key_env: str,
    embedding_api_key_file: str | None,
    device: str,
) -> list[dict[str, Any]]:
    db = lancedb_module.connect(str(lancedb_path))
    if "chunks" not in lancedb_table_names(db):
        return []
    table = db.open_table("chunks")
    if embedding_backend == "zhipu":
        query_vector = zhipu_query_embedding(query, api_key_env=api_key_env, model=embedding_model, dimensions=dimensions)
    elif embedding_backend == "http":
        query_vector = http_query_embedding(query, endpoint=embedding_endpoint, api_key_env=embedding_api_key_env, api_key_file=embedding_api_key_file, model=embedding_model)
    else:
        if sentence_transformers_cls is None:
            raise RuntimeError("local semantic query requires sentence-transformers")
        model = sentence_transformers_cls(embedding_model, device=device)
        query_vector = model.encode([query], convert_to_numpy=True, normalize_embeddings=False)[0].tolist()
    search = table.search(query_vector).limit(top_k * 4)
    try:
        rows = search.to_pandas().to_dict(orient="records")
    except Exception:
        rows = search.to_arrow().to_pylist()
    results: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for row in rows:
        relative_path = str(row.get("relative_path", ""))
        if not in_requested_scope(relative_path, scope):
            continue
        path_key = relative_path
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        chunk_text = str(row.get("chunk_text", ""))
        results.append(
            {
                "path": path_key,
                "absolute_path": row.get("absolute_path", ""),
                "title": row.get("title", ""),
                "score": round(float(row.get("_distance", row.get("score", 0.0))), 4),
                "match_type": ["semantic"],
                "heading_path": row.get("heading_path", ""),
                "snippet": snippet_for(chunk_text, terms_for(query)),
                "chunk_text": redact_text(chunk_text),
                "mtime": row.get("mtime", ""),
                "source_type": row.get("source_type", ""),
            }
        )
        if len(results) >= top_k:
            break
    return results


def opencode_semantic_query(
    *,
    lancedb_module: Any,
    lancedb_path: Path,
    query: str,
    top_k: int,
    embedding_model: str,
    dimensions: int,
    embedding_endpoint: str,
    embedding_api_key_env: str,
    embedding_api_key_file: str | None,
) -> list[dict[str, Any]]:
    db = lancedb_module.connect(str(lancedb_path))
    if OPCODE_TABLE not in lancedb_table_names(db):
        return []
    table = db.open_table(OPCODE_TABLE)
    query_vector = http_query_embedding(
        query,
        endpoint=embedding_endpoint,
        api_key_env=embedding_api_key_env,
        api_key_file=embedding_api_key_file,
        model=embedding_model,
    )
    search = table.search(query_vector).limit(top_k * 4)
    try:
        rows = search.to_pandas().to_dict(orient="records")
    except Exception:
        rows = search.to_arrow().to_pylist()
    results: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get("session_id", ""))
        message_id = str(row.get("message_id", ""))
        chunk_text = str(row.get("chunk_text", ""))
        results.append(
            {
                "path": f"opencode://{sid}",
                "session_id": sid,
                "message_id": message_id,
                "absolute_path": "",
                "title": row.get("session_title", ""),
                "score": round(float(row.get("_distance", row.get("score", 0.0))), 4),
                "match_type": ["semantic"],
                "heading_path": f"message/{row.get('message_role', '')}",
                "snippet": snippet_for(chunk_text, terms_for(query)),
                "chunk_text": redact_text(chunk_text),
                "mtime": "",
                "source_type": "opencode_session",
            }
        )
        if len(results) >= top_k:
            break
    return results


def opencode_sql_keyword_query(
    db_path: Path,
    query: str,
    top_k: int,
) -> list[dict[str, Any]]:
    import sqlite3
    if not db_path.exists():
        return []
    terms = terms_for(query)
    if not terms:
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in terms[:5]:
        pattern = f"%{term}%"
        rows = con.execute(
            "SELECT s.id as session_id, s.title as session_title, m.id as message_id, m.time_created, m.data FROM message m JOIN session s ON m.session_id = s.id WHERE m.data LIKE ? ORDER BY m.time_created DESC LIMIT ?",
            (pattern, top_k * 3),
        ).fetchall()
        for r in rows:
            d = dict(r)
            sid = d["session_id"]
            if sid in seen:
                continue
            seen.add(sid)
            try:
                parsed = json.loads(d["data"])
            except Exception:
                parsed = {}
            role = parsed.get("role", "unknown")
            parts_rows = con.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY id LIMIT 3",
                (d["message_id"],),
            ).fetchall()
            snippet_parts = []
            for pr in parts_rows:
                try:
                    pd_ = json.loads(pr["data"])
                    txt = pd_.get("text", "")
                    if txt:
                        snippet_parts.append(txt[:200])
                except Exception:
                    pass
            snippet = " ".join(snippet_parts)[:300]
            results.append(
                {
                    "path": f"opencode://{sid}",
                    "session_id": sid,
                    "absolute_path": "",
                    "title": d.get("session_title", ""),
                    "score": 1.0,
                    "match_type": ["keyword"],
                    "heading_path": f"message/{role}",
                    "snippet": redact_text(snippet),
                    "chunk_text": redact_text(snippet),
                    "mtime": "",
                    "source_type": "opencode_session",
                }
            )
            if len(results) >= top_k:
                break
        if len(results) >= top_k:
            break
    con.close()
    return results


def scan_markdown_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in root.rglob("*.md"):
        if ".git" in path.parts or ".obsidian" in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if is_migrated_markdown_path(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
        records.append({"doc_id": rel, "absolute_path": str(path), "relative_path": rel, "title": path.stem, "chunk_text": text[:4000], "heading_path": "", "mtime": dt.datetime.fromtimestamp(stat.st_mtime, dt.UTC).isoformat().replace("+00:00", "Z"), "frontmatter": {}, "wikilinks": [], "tags": [], "source_type": "markdown_live_fallback"})
    return records


def query_records(records: list[dict[str, Any]], query: str, top_k: int, scope: list[str] | None) -> list[dict[str, Any]]:
    terms = terms_for(query)
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for record in records:
        relative_path = str(record.get("relative_path", ""))
        if not in_requested_scope(relative_path, scope):
            continue
        score, match_types = score_record(record, query, terms)
        if score <= 0:
            continue
        scored.append((score, record, match_types))
    scored.sort(key=lambda item: (item[0], str(item[1].get("mtime", ""))), reverse=True)
    results: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for score, record, match_types in scored:
        key = str(record.get("relative_path", ""))
        if key in seen_paths:
            continue
        seen_paths.add(key)
        chunk_text = str(record.get("chunk_text", ""))
        results.append(
            {
                "path": key,
                "absolute_path": record.get("absolute_path", ""),
                "title": record.get("title", ""),
                "score": round(score, 4),
                "match_type": match_types,
                "heading_path": record.get("heading_path", ""),
                "snippet": snippet_for(chunk_text, terms),
                "chunk_text": redact_text(chunk_text),
                "mtime": record.get("mtime", ""),
                "source_type": record.get("source_type", ""),
            }
        )
        if len(results) >= top_k:
            break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query the derived LanceDB cache path. Falls back to JSONL/live keyword search when semantic deps or cache are unavailable.",
    )
    parser.add_argument("query", nargs="?", help="Natural language or keyword query.")
    parser.add_argument("--root", default=os.getcwd(), help="Repository root for live keyword fallback. Default: current directory.")
    parser.add_argument("--top-k", type=int, default=10, help="Maximum number of deduplicated results.")
    parser.add_argument("--scope", action="append", help="Limit results to relative scope prefix. Repeatable.")
    parser.add_argument("--manifest", default=None, help="Manifest path. Default: <cache-dir>/index-manifest.json")
    parser.add_argument("--cache-dir", default=str(CACHE_BASE), help="Cache base. Default: ~/.cache/agent-knowledge/Zettelkasten")
    parser.add_argument("--mode", choices=["semantic", "keyword", "auto"], default="auto", help="Search mode. Default: auto.")
    parser.add_argument("--embedding-backend", choices=["auto", "local", "zhipu", "http"], default="auto", help="Embedding backend for semantic query. Default: auto from manifest.")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="Embedding model for semantic vector search.")
    parser.add_argument("--dimensions", type=int, default=0, help="Embedding dimensions for zhipu semantic query. Default: manifest value or 1024.")
    parser.add_argument("--zhipu-api-key-env", default="ZHIPU_API_KEY", help="Environment variable containing Zhipu API key.")
    parser.add_argument("--embedding-endpoint", default=DEFAULT_HTTP_EMBEDDING_ENDPOINT, help="HTTP embedding endpoint for --embedding-backend=http.")
    parser.add_argument("--embedding-api-key-env", default=DEFAULT_HTTP_EMBEDDING_API_KEY_ENV, help="Environment variable containing HTTP embedding API key.")
    parser.add_argument("--embedding-api-key-file", default=str(DEFAULT_HTTP_EMBEDDING_API_KEY_FILE), help="File containing HTTP embedding API key. Used when env is unset.")
    parser.add_argument("--device", default="cpu", help="Embedding device. Default: cpu.")
    parser.add_argument("--source", choices=["markdown", "opencode", "all"], default="all", help="Data sources to query. Default: all.")
    parser.add_argument("--opencode-db", default=str(DEFAULT_OPCODE_DB), help="OpenCode SQLite database for keyword fallback.")
    args = parser.parse_args()

    if not args.query:
        parser.error("query is required unless --help is used")
    cache_base = Path(args.cache_dir).expanduser().resolve()
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else cache_base / "index-manifest.json"
    lancedb_path = cache_base / "lancedb"
    chunks_jsonl = lancedb_path / "chunks.jsonl"
    manifest = load_json(manifest_path)
    records = load_records(chunks_jsonl)
    backend = manifest.get("backend", "jsonl_keyword_fallback") if manifest else "jsonl_keyword_fallback"
    embedding_backend = args.embedding_backend if args.embedding_backend != "auto" else str(manifest.get("embedding_backend", "local") if manifest else "local")
    if embedding_backend == "zhipu":
        if args.embedding_model == DEFAULT_EMBEDDING_MODEL:
            args.embedding_model = str(manifest.get("embedding_model", DEFAULT_ZHIPU_EMBEDDING_MODEL) if manifest else DEFAULT_ZHIPU_EMBEDDING_MODEL)
        if not args.dimensions:
            args.dimensions = int(manifest.get("dimensions", DEFAULT_ZHIPU_DIMENSIONS) if manifest else DEFAULT_ZHIPU_DIMENSIONS)
    elif embedding_backend == "http":
        if args.embedding_model == DEFAULT_EMBEDDING_MODEL:
            args.embedding_model = str(manifest.get("embedding_model", DEFAULT_HTTP_EMBEDDING_MODEL) if manifest else DEFAULT_HTTP_EMBEDDING_MODEL)
        if not args.dimensions:
            args.dimensions = int(manifest.get("dimensions", DEFAULT_HTTP_DIMENSIONS) if manifest else DEFAULT_HTTP_DIMENSIONS)
        if args.embedding_endpoint == DEFAULT_HTTP_EMBEDDING_ENDPOINT and manifest.get("embedding_endpoint"):
            args.embedding_endpoint = str(manifest.get("embedding_endpoint"))
    fallback_reason = manifest.get("fallback", {}).get("reason", "Using JSONL cache from LanceDB cache path.") if manifest else "Using JSONL cache from LanceDB cache path."
    mode = "keyword_fallback"
    lancedb_module, sentence_transformers_cls, import_error = optional_vector_backend()
    results: list[dict[str, Any]] = []
    if args.source != "opencode":
        if not records:
            records = scan_markdown_records(Path(args.root).expanduser().resolve())
            backend = "live_keyword_fallback"
            fallback_reason = "JSONL cache missing or empty; scanned Markdown files directly. Run build_lancedb_index.py to refresh cache."
            results = query_records(records, args.query, args.top_k, args.scope)
        elif args.mode in {"semantic", "auto"} and lancedb_module and (embedding_backend in {"zhipu", "http"} or sentence_transformers_cls):
            try:
                results = semantic_query(
                    lancedb_module=lancedb_module,
                    sentence_transformers_cls=sentence_transformers_cls,
                    lancedb_path=lancedb_path,
                    query=args.query,
                    top_k=args.top_k,
                    scope=args.scope,
                    embedding_model=args.embedding_model,
                    embedding_backend=embedding_backend,
                    dimensions=args.dimensions,
                    api_key_env=args.zhipu_api_key_env,
                    embedding_endpoint=args.embedding_endpoint,
                    embedding_api_key_env=args.embedding_api_key_env,
                    embedding_api_key_file=args.embedding_api_key_file,
                    device=args.device,
                )
                if results:
                    mode = "semantic_vector"
                elif args.mode == "semantic":
                    mode = "semantic_vector"
            except Exception as exc:  # pragma: no cover - optional deps/runtime
                fallback_reason = f"Semantic vector search failed ({safe_exception_summary(exc)}); falling back to keyword search."
                if args.mode == "semantic":
                    results = []
            if not results and args.mode == "auto":
                results = query_records(records, args.query, args.top_k, args.scope)
                mode = "keyword_fallback"
        else:
            if args.mode == "semantic" and import_error:
                fallback_reason = f"{import_error}; semantic vector search unavailable."
            results = query_records(records, args.query, args.top_k, args.scope)

    opencode_results: list[dict[str, Any]] = []
    opencode_mode = ""
    opencode_manifest = load_json(cache_base / "opencode-index-manifest.json")
    opencode_backend = opencode_manifest.get("backend", "") if opencode_manifest else ""

    if args.source in ("opencode", "all") and lancedb_module and opencode_backend == "lancedb_semantic_vector":
        try:
            opencode_results = opencode_semantic_query(
                lancedb_module=lancedb_module,
                lancedb_path=lancedb_path,
                query=args.query,
                top_k=args.top_k,
                embedding_model=args.embedding_model,
                dimensions=args.dimensions,
                embedding_endpoint=args.embedding_endpoint,
                embedding_api_key_env=args.embedding_api_key_env,
                embedding_api_key_file=args.embedding_api_key_file,
            )
            if opencode_results:
                opencode_mode = "semantic_vector"
        except Exception:
            pass

    if args.source in ("opencode", "all") and not opencode_results and args.mode != "semantic":
        opencode_db = Path(args.opencode_db).expanduser()
        opencode_results = opencode_sql_keyword_query(opencode_db, args.query, args.top_k)
        opencode_mode = "sql_keyword" if opencode_results else ""

    if opencode_results:
        merged: list[dict[str, Any]] = []
        markdown_iter = iter(results)
        opencode_iter = iter(opencode_results)
        md_next = next(markdown_iter, None)
        oc_next = next(opencode_iter, None)
        while len(merged) < args.top_k and (md_next is not None or oc_next is not None):
            md_score = md_next.get("score", 0) if md_next else -1
            oc_score = oc_next.get("score", 0) if oc_next else -1
            if md_score >= oc_score:
                merged.append(md_next)
                md_next = next(markdown_iter, None)
            else:
                merged.append(oc_next)
                oc_next = next(opencode_iter, None)
        results = merged
        if mode == "keyword_fallback" and opencode_mode == "semantic_vector":
            mode = "hybrid_vector"
        elif opencode_mode == "semantic_vector":
            mode = f"{mode}+opencode_semantic"

    output = {
        "query": args.query,
        "mode": mode,
        "top_k": args.top_k,
        "index_status": {
            "backend": backend,
            "manifest_path": str(manifest_path),
            "lancedb_path": str(lancedb_path),
            "chunks_jsonl": str(chunks_jsonl),
            "manifest_found": bool(manifest),
            "freshness_verified": bool(manifest),
            "fallback": {"enabled": mode != "semantic_vector", "reason": fallback_reason},
            "excluded_markdown_scopes": list(MIGRATED_MARKDOWN_SCOPES),
            "opencode": {
                "enabled": bool(opencode_results),
                "mode": opencode_mode,
                "manifest_found": bool(opencode_manifest),
            },
        },
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
