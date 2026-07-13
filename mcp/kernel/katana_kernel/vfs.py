"""GovernedVFS: root confinement, no path traversal/symlink escape, write via policy."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from katana_kernel.policy import DomainPolicy


class VFSError(Exception):
    """Raised for VFS governance violations."""


class GovernedVFS:
    def __init__(self, root: str, policy: DomainPolicy | None = None):
        self._root = Path(root).resolve()
        if not self._root.is_dir():
            raise VFSError(f"VFS root is not a directory: {self._root}")
        self._policy = policy

    @property
    def root(self) -> str:
        return str(self._root)

    def _resolve(self, p: str | Path) -> Path:
        p = Path(p)
        if p.is_absolute():
            raise VFSError(f"absolute paths not allowed: {p}")
        if ".." in p.parts:
            raise VFSError(f"path traversal not allowed: {p}")
        parts = p.parts
        current = self._root
        for part in parts:
            if part in (".", ""):
                continue
            candidate = current / part
            if candidate.is_symlink():
                raise VFSError(f"symlink not allowed in governed path: {candidate}")
            current = candidate
        resolved = current.resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise VFSError(f"path escapes root: {p}")
        return resolved

    def _check_write(self, op: str, args: dict | None = None):
        if self._policy is not None:
            self._policy.verify(op, args or {})

    def read_text(self, path: str) -> str:
        resolved = self._resolve(path)
        return resolved.read_text(encoding="utf-8")

    def read_bytes(self, path: str) -> bytes:
        resolved = self._resolve(path)
        return resolved.read_bytes()

    def write(self, path: str, content: str, op: str = "write", args: dict | None = None):
        self._check_write(op, args)
        resolved = self._resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

    def write_bytes(self, path: str, content: bytes, op: str = "write", args: dict | None = None):
        self._check_write(op, args)
        resolved = self._resolve(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_bytes(content)

    def delete(self, path: str, op: str = "delete", args: dict | None = None):
        self._check_write(op, args)
        resolved = self._resolve(path)
        if resolved.is_file():
            resolved.unlink()
        elif resolved.is_dir():
            shutil.rmtree(str(resolved))

    def exists(self, path: str) -> bool:
        resolved = self._resolve(path)
        return resolved.exists()

    def is_file(self, path: str) -> bool:
        resolved = self._resolve(path)
        return resolved.is_file()

    def is_dir(self, path: str) -> bool:
        resolved = self._resolve(path)
        return resolved.is_dir()

    def rename(self, old_path: str, new_path: str, op: str = "rename", args: dict | None = None):
        self._check_write(op, args)
        src = self._resolve(old_path)
        dst = self._resolve(new_path)
        if dst.exists():
            raise VFSError(f"rename target already exists: {new_path}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)

    def ls(self, pattern: str = "*.md") -> list[str]:
        if "/" in pattern or "*" in pattern or "?" in pattern:
            return sorted(
                str(p.relative_to(self._root))
                for p in self._root.glob(pattern)
                if not p.name.startswith(".") and p.is_file()
            )
        resolved = self._resolve(pattern) if pattern and pattern != "." else self._root
        return sorted(
            str(p.relative_to(self._root))
            for p in resolved.iterdir()
            if not p.name.startswith(".") and p.is_file()
        )

    def mkdir(self, path: str, op: str = "mkdir", args: dict | None = None):
        self._check_write(op, args)
        resolved = self._resolve(path)
        resolved.mkdir(parents=True, exist_ok=True)

    def stat(self, path: str) -> dict:
        resolved = self._resolve(path)
        if not resolved.exists():
            raise VFSError(f"path not found: {path}")
        st = resolved.stat()
        return {
            "size": st.st_size,
            "mtime": st.st_mtime,
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
        }