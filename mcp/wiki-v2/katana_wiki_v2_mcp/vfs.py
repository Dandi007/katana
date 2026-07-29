"""v2 wiki VFS — read-only four-tool surface.

fs_read / fs_list / fs_glob / fs_stat only.
No fs_write/fs_create/fs_edit/fs_copy/fs_rename/fs_delete/fs_batch (INV-1).
"""
from __future__ import annotations

import os
from pathlib import Path


def fs_read(data_root: str, path: str, offset: int | None = None, limit: int | None = None) -> dict:
    full = _safe_path(data_root, path)
    if full is None:
        return {"code": "INVALID_PATH", "message": f"invalid path: {path}"}
    if not full.is_file():
        return {"code": "RESOURCE_NOT_FOUND", "message": f"file not found: {path}"}
    try:
        content = full.read_text(encoding="utf-8")
    except Exception as e:
        return {"code": "OPERATION_FAILED", "message": str(e)}

    lines = content.split("\n")
    total = len(lines)
    start = max(1, offset or 1)
    last = min(total, start + limit - 1) if limit is not None else total
    if start > total or start > last:
        rendered = ""
    else:
        rendered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, last + 1))

    return {
        "path": path,
        "content": content,
        "rendered": rendered,
        "total_lines": total,
        "offset": start,
        "limit": limit,
    }


def fs_list(data_root: str, path: str = "") -> dict:
    if path:
        full = _safe_path(data_root, path)
        if full is None:
            return {"code": "INVALID_PATH", "message": f"invalid path: {path}"}
        if not full.is_dir():
            return {"code": "INVALID_PATH", "message": f"not a directory: {path}"}
        entries = []
        for p in sorted(full.iterdir()):
            entries.append(_entry(p, data_root))
        return {"path": path, "entries": entries}
    else:
        entries = []
        for p in sorted(Path(data_root).iterdir()):
            if p.name.startswith("."):
                continue
            entries.append(_entry(p, data_root))
        return {"path": "", "entries": entries}


def fs_glob(data_root: str, pattern: str) -> dict:
    if ".." in pattern or pattern.startswith("/"):
        return {"code": "INVALID_PATH", "message": f"invalid glob pattern: {pattern}"}
    root = Path(data_root)
    hits = sorted(root.glob(pattern))
    entries = [_entry(p, data_root) for p in hits]
    return {"pattern": pattern, "hits": [str(p.relative_to(root)) for p in hits], "entries": entries}


def fs_stat(data_root: str, path: str) -> dict:
    full = _safe_path(data_root, path)
    if full is None:
        return {"code": "INVALID_PATH", "message": f"invalid path: {path}"}
    if not full.exists():
        return {"code": "RESOURCE_NOT_FOUND", "message": f"not found: {path}"}
    try:
        st = full.stat()
        return {
            "path": path,
            "node_type": "directory" if full.is_dir() else "file",
            "size": st.st_size if full.is_file() else None,
            "mtime": st.st_mtime,
        }
    except Exception as e:
        return {"code": "OPERATION_FAILED", "message": str(e)}


def _safe_path(data_root: str, path: str) -> Path | None:
    root = Path(data_root).resolve()
    candidate = (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _entry(p: Path, data_root: str) -> dict:
    rel = str(p.relative_to(data_root))
    try:
        st = p.stat()
        return {
            "path": rel,
            "node_type": "directory" if p.is_dir() else "file",
            "size": st.st_size if p.is_file() else None,
            "mtime": st.st_mtime,
        }
    except Exception:
        return {
            "path": rel,
            "node_type": "directory" if p.is_dir() else "file",
            "size": None,
            "mtime": 0,
        }