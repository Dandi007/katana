"""Canonical read + node metadata contract (design §5.2 read responses).

Every successful read/discovery response returns a uniform node descriptor:
resource_id, virtual_path, node type, size, media type, content hash,
resource_revision, content_revision and the pinned snapshot commit. Reads come
from the canonical tree; host physical paths never leak.
"""
from __future__ import annotations

import mimetypes
from dataclasses import asdict, dataclass

from . import identity


@dataclass
class NodeDescriptor:
    resource_id: str
    virtual_path: str
    node_type: str            # "file" | "dir"
    size: int
    media_type: str
    content_hash: str
    resource_revision: str
    content_revision: str
    snapshot_commit: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def guess_media_type(path: str) -> str:
    mt, _ = mimetypes.guess_type(path)
    if mt:
        return mt
    if path.endswith(".md"):
        return "text/markdown"
    return "application/octet-stream"


def describe(*, resource_id: str, virtual_path: str, content: bytes | str,
             snapshot_commit: str | None, node_type: str = "file",
             metadata_hash: str = "") -> NodeDescriptor:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return NodeDescriptor(
        resource_id=resource_id,
        virtual_path=virtual_path,
        node_type=node_type,
        size=len(raw),
        media_type=guess_media_type(virtual_path),
        content_hash=identity.content_hash(raw),
        resource_revision=identity.resource_revision(
            resource_id=resource_id, virtual_path=virtual_path,
            content=raw, metadata_hash=metadata_hash),
        content_revision=identity.content_revision(raw),
        snapshot_commit=snapshot_commit,
    )


def render_lines(text: str, *, offset: int | None = None,
                 limit: int | None = None) -> dict:
    """FS-Read semantics: cat -n rendering with 1-based offset/limit paging."""
    lines = text.split("\n")
    total = len(lines)
    start = max(1, offset or 1)
    last = min(total, start + limit - 1) if limit is not None else total
    if start > total or start > last:
        rendered = ""
    else:
        rendered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, last + 1))
    return {"total_lines": total, "offset": start, "limit": limit,
            "content": rendered}
