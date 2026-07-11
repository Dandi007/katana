"""Virtual path confinement (design §5.2, §7.2, INV-1).

A virtual path is a readable, mutable locator *inside* a single data repo. It
never leaks a host filesystem path. Confinement rejects traversal, absolute
paths, encoded ``..``, alternate separators, NUL bytes, reserved namespaces and
symlink-style escapes *before* any lookup or mutation.
"""
from __future__ import annotations

import posixpath
import unicodedata

from .errors import INVALID_PATH, KernelError

# Reserved namespace prefixes that ordinary fs_* traffic can neither see nor
# write (design §4.3 / §7.2). Server-managed catalogs live here.
RESERVED_PREFIXES = (".kb", ".git")


def _reject(path: str, reason: str) -> KernelError:
    return KernelError(INVALID_PATH, f"invalid virtual path {path!r}: {reason}",
                       virtual_path=path)


def normalize(path: str) -> str:
    """Return a confined, canonical, repo-relative POSIX path.

    Raises ``KernelError(INVALID_PATH)`` on any escape attempt. The result never
    starts with ``/`` and never contains ``.``/``..`` segments.
    """
    if not isinstance(path, str) or path == "":
        raise _reject(str(path), "empty path")
    if "\x00" in path:
        raise _reject(path, "NUL byte")
    # Alternate separators are not allowed; canonical separator is '/'.
    if "\\" in path:
        raise _reject(path, "backslash separator")
    # NFC-normalize to defeat Unicode/casefold collisions at the boundary.
    norm = unicodedata.normalize("NFC", path)
    if norm.startswith("/"):
        raise _reject(path, "absolute path")
    # Collapse and validate each segment.
    parts: list[str] = []
    for seg in norm.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise _reject(path, "parent traversal")
        parts.append(seg)
    if not parts:
        raise _reject(path, "resolves to repo root")
    cleaned = posixpath.join(*parts)
    # posixpath.normpath as defence-in-depth; must not re-introduce traversal.
    if posixpath.normpath(cleaned) != cleaned:
        raise _reject(path, "non-canonical path")
    return cleaned


def is_reserved(path: str) -> bool:
    """True if the (already-normalized) path lives in a reserved namespace."""
    top = path.split("/", 1)[0]
    return top in RESERVED_PREFIXES


def confine(path: str) -> str:
    """Normalize and reject reserved-namespace access for ordinary traffic."""
    cleaned = normalize(path)
    if is_reserved(cleaned):
        raise _reject(path, "reserved namespace")
    return cleaned


def confined_join(root: str, path: str) -> str:
    """Return an absolute path for a confined virtual path under ``root``.

    This is for writer-private staging/domain helper IO. It rejects absolute
    paths/traversal via :func:`confine` and then verifies the real parent stays
    under the real staging root so an existing symlink parent cannot redirect a
    write outside the repo.
    """
    import os

    rel = confine(path)
    real_root = os.path.realpath(root)
    target = os.path.abspath(os.path.join(root, rel))
    parent = os.path.dirname(target) or root
    real_parent = os.path.realpath(parent)
    if real_parent != real_root and not real_parent.startswith(real_root + os.sep):
        raise _reject(path, "symlink parent escapes root")
    if os.path.islink(target):
        raise _reject(path, "symlink target")
    return target
