"""Governed Full VFS façade (design §5.2, INV-5, INV-10).

`GovernedVFS` implements the mechanical `fs_*` operation set on top of the
transaction engine, identity catalog and a domain policy. Domain tools and
`fs_*` both compile into the SAME MutationBatch and flow through the SAME
policy → transaction pipeline — there is no raw bypass (INV-10): every mutation
is authorized, CAS-checked, projected, validated and Git-published.

Phase-1 scope: whole/range read, create (mints id), write (no implicit create),
exact-match edit, mkdir, copy (new id), rename (keeps id), delete (tombstone),
and single-repo `fs_batch`. No file descriptors, seek, symlink or raw catalog
edit.
"""
from __future__ import annotations

import glob as _glob
import os

from . import paths, vfs
from .batch import Change, MutationBatch, Op
from .catalog import Catalog
from .errors import (
    KernelError,
    NOT_FOUND,
    POLICY_VIOLATION,
    REF_MISMATCH,
    RESOURCE_REPLACED,
)
from .transaction import TransactionEngine


class GovernedVFS:
    def __init__(self, engine: TransactionEngine, catalog: Catalog, policy) -> None:
        self.engine = engine
        self.catalog = catalog
        self.policy = policy
        self.repo_root = engine.repo_root

    # ── helpers ───────────────────────────────────────────────────────
    def _abs(self, rel: str) -> str:
        return os.path.join(self.repo_root, rel)

    def _read_bytes(self, rel: str) -> bytes | None:
        p = self._abs(rel)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def _resolve_target(self, *, resource_id: str | None,
                        virtual_path: str | None,
                        expected_resource_id: str | None = None) -> tuple[str, str | None]:
        """Return (confined_path, resource_id?). ID is primary; path is a locator.

        Raises REF_MISMATCH when both are given but disagree, and
        RESOURCE_REPLACED when the path is now owned by a different id
        (ABA guard, design §5.3).
        """
        rid = resource_id
        path = paths.confine(virtual_path) if virtual_path else None
        if rid is not None:
            cat_path = self.catalog.path_of(rid)
            if cat_path is None:
                raise KernelError(NOT_FOUND, f"unknown resource_id {rid}",
                                  resource_id=rid)
            if path is not None and path != cat_path:
                raise KernelError(REF_MISMATCH,
                                  "resource_id and virtual_path resolve to "
                                  "different objects", resource_id=rid,
                                  virtual_path=path)
            return cat_path, rid
        if path is not None:
            owner = self.catalog.id_of(path)
            if expected_resource_id and owner and owner != expected_resource_id:
                raise KernelError(RESOURCE_REPLACED,
                                  "path is now owned by a different resource",
                                  virtual_path=path, resource_id=owner)
            return path, owner
        raise KernelError(POLICY_VIOLATION,
                          "either resource_id or virtual_path is required")

    def _descriptor(self, rid: str | None, rel: str, raw: bytes) -> vfs.NodeDescriptor:
        return vfs.describe(resource_id=rid or "", virtual_path=rel, content=raw,
                            snapshot_commit=self.engine.repo.head())

    def _commit(self, batch: MutationBatch, message: str):
        self.policy.validate(batch)
        return self.engine.commit(batch, message=message)

    # ── discovery / read ──────────────────────────────────────────────
    def fs_stat(self, *, resource_id: str | None = None,
                virtual_path: str | None = None) -> dict:
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        raw = self._read_bytes(rel)
        if raw is None:
            if os.path.isdir(self._abs(rel)):
                return vfs.describe(resource_id=rid or "", virtual_path=rel,
                                    content=b"", snapshot_commit=self.engine.repo.head(),
                                    node_type="dir").to_dict()
            raise KernelError(NOT_FOUND, f"no such node: {rel}",
                              virtual_path=rel)
        return self._descriptor(rid, rel, raw).to_dict()

    def fs_resolve(self, virtual_path: str) -> dict:
        rel = paths.confine(virtual_path)
        rid = self.catalog.id_of(rel)
        return {"resource_id": rid, "virtual_path": rel,
                "exists": os.path.exists(self._abs(rel))}

    def fs_list(self, virtual_path: str = "") -> list[dict]:
        base = paths.normalize(virtual_path) if virtual_path else ""
        abs_base = self._abs(base) if base else self.repo_root
        if not os.path.isdir(abs_base):
            raise KernelError(NOT_FOUND, f"not a directory: {virtual_path}",
                              virtual_path=virtual_path)
        out: list[dict] = []
        for name in sorted(os.listdir(abs_base)):
            rel = f"{base}/{name}" if base else name
            if paths.is_reserved(rel):
                continue
            abs_p = os.path.join(abs_base, name)
            if os.path.isdir(abs_p):
                out.append({"virtual_path": rel, "node_type": "dir",
                            "resource_id": None})
            else:
                out.append({"virtual_path": rel, "node_type": "file",
                            "resource_id": self.catalog.id_of(rel)})
        return out

    def fs_glob(self, pattern: str) -> list[str]:
        matches = _glob.glob(os.path.join(self.repo_root, pattern),
                             recursive=True)
        rels = []
        for m in matches:
            rel = os.path.relpath(m, self.repo_root)
            if paths.is_reserved(rel.replace(os.sep, "/")):
                continue
            if os.path.isfile(m):
                rels.append(rel.replace(os.sep, "/"))
        return sorted(rels)

    def fs_read(self, *, resource_id: str | None = None,
                virtual_path: str | None = None, offset: int | None = None,
                limit: int | None = None) -> dict:
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        raw = self._read_bytes(rel)
        if raw is None:
            raise KernelError(NOT_FOUND, f"no such file: {rel}", virtual_path=rel)
        desc = self._descriptor(rid, rel, raw).to_dict()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            desc["binary"] = True
            return desc
        desc.update(vfs.render_lines(text, offset=offset, limit=limit))
        return desc

    # ── create / write / edit / mkdir ─────────────────────────────────
    def fs_create(self, *, virtual_path: str, content: str,
                  mutation_id: str | None = None) -> dict:
        rel = paths.confine(virtual_path)
        if os.path.exists(self._abs(rel)):
            raise KernelError(POLICY_VIOLATION, f"path exists: {rel}",
                              virtual_path=rel)
        rid = self.catalog.mint(rel)
        raw = content.encode("utf-8")
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.CREATE, resource_id=rid, after_path=rel,
                         after_content=raw, after_hash=vfs.identity.content_hash(raw)))
        res = self._commit(batch, f"feat({self.engine.domain}): fs_create {rel} ({rid})")
        return self._result(res, rid, rel)

    def fs_write(self, *, resource_id: str | None = None,
                 virtual_path: str | None = None, content: str,
                 expected_base_commit: str | None = None,
                 mutation_id: str | None = None) -> dict:
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        if not os.path.exists(self._abs(rel)):
            raise KernelError(NOT_FOUND,
                              "fs_write does not implicitly create; use fs_create",
                              virtual_path=rel)
        raw = content.encode("utf-8")
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              expected_base_commit=expected_base_commit)
        batch.add(Change(op=Op.WRITE, resource_id=rid or "", after_path=rel,
                         after_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_write {rel}")
        return self._result(res, rid, rel)

    def fs_edit(self, *, resource_id: str | None = None,
                virtual_path: str | None = None, old_string: str,
                new_string: str, replace_all: bool = False,
                mutation_id: str | None = None) -> dict:
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        raw = self._read_bytes(rel)
        if raw is None:
            raise KernelError(NOT_FOUND, f"no such file: {rel}", virtual_path=rel)
        text = raw.decode("utf-8")
        if not old_string:
            raise KernelError(POLICY_VIOLATION, "old_string must be non-empty")
        if old_string == new_string:
            raise KernelError(POLICY_VIOLATION,
                              "old_string must differ from new_string")
        count = text.count(old_string)
        if count == 0:
            raise KernelError(POLICY_VIOLATION,
                              f"old_string not found in {rel}", virtual_path=rel)
        if count > 1 and not replace_all:
            raise KernelError(POLICY_VIOLATION,
                              f"old_string matches {count} times; narrow it or "
                              "pass replace_all", virtual_path=rel)
        new_text = (text.replace(old_string, new_string) if replace_all
                    else text.replace(old_string, new_string, 1))
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.EDIT, resource_id=rid or "", after_path=rel,
                         after_content=new_text.encode("utf-8")))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_edit {rel}")
        return self._result(res, rid, rel)

    def fs_mkdir(self, *, virtual_path: str) -> dict:
        rel = paths.confine(virtual_path)
        os.makedirs(self._abs(rel), exist_ok=True)
        # Git does not track empty dirs; add a keep marker so it is durable.
        keep = os.path.join(self._abs(rel), ".gitkeep")
        if not os.path.exists(keep):
            with open(keep, "w", encoding="utf-8"):
                pass
        batch = MutationBatch(domain=self.engine.domain)
        batch.add(Change(op=Op.CREATE, resource_id="", after_path=f"{rel}/.gitkeep",
                         after_content=b""))
        res = self.engine.commit(batch, message=f"chore({self.engine.domain}): fs_mkdir {rel}")
        return {"virtual_path": rel, "commit_sha": res.commit_sha}

    # ── structure ─────────────────────────────────────────────────────
    def fs_rename(self, *, resource_id: str | None = None,
                  virtual_path: str | None = None, new_path: str,
                  mutation_id: str | None = None) -> dict:
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        dst = paths.confine(new_path)
        if os.path.exists(self._abs(dst)):
            raise KernelError(POLICY_VIOLATION, f"destination exists: {dst}",
                              virtual_path=dst)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.RENAME, resource_id=rid or "",
                         before_path=rel, after_path=dst))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_rename {rel} -> {dst}")
        if rid:
            self.catalog.rebind(rid, dst)
        return self._result(res, rid, dst)

    def fs_copy(self, *, resource_id: str | None = None,
                virtual_path: str | None = None, new_path: str,
                mutation_id: str | None = None) -> dict:
        rel, _ = self._resolve_target(resource_id=resource_id,
                                      virtual_path=virtual_path)
        dst = paths.confine(new_path)
        raw = self._read_bytes(rel)
        if raw is None:
            raise KernelError(NOT_FOUND, f"no such file: {rel}", virtual_path=rel)
        if os.path.exists(self._abs(dst)):
            raise KernelError(POLICY_VIOLATION, f"destination exists: {dst}",
                              virtual_path=dst)
        new_rid = self.catalog.mint(dst)  # copy mints a NEW id
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.COPY, resource_id=new_rid, after_path=dst,
                         after_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_copy {rel} -> {dst}")
        return self._result(res, new_rid, dst)

    def fs_delete(self, *, resource_id: str | None = None,
                  virtual_path: str | None = None,
                  mutation_id: str | None = None) -> dict:
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        if not os.path.exists(self._abs(rel)):
            raise KernelError(NOT_FOUND, f"no such file: {rel}", virtual_path=rel)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.DELETE, resource_id=rid or "", before_path=rel))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_delete {rel}")
        if rid:
            self.catalog.tombstone(rid)  # tombstone; id never reused
        return {"resource_id": rid, "virtual_path": rel,
                "deleted": True, "commit_sha": res.commit_sha}

    # ── batch ─────────────────────────────────────────────────────────
    def fs_batch(self, changes: list[dict], *,
                 expected_base_commit: str | None = None,
                 mutation_id: str | None = None) -> dict:
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              expected_base_commit=expected_base_commit)
        for spec in changes:
            op = Op(spec["op"])
            rel = paths.confine(spec["virtual_path"]) if spec.get("virtual_path") else None
            rid = spec.get("resource_id")
            content = spec.get("content")
            raw = content.encode("utf-8") if content is not None else None
            if op in (Op.CREATE, Op.COPY) and rel and not rid:
                rid = self.catalog.mint(rel)
            batch.add(Change(op=op, resource_id=rid or "",
                             before_path=spec.get("from_path"),
                             after_path=rel, after_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_batch "
                                  f"{len(changes)} change(s)")
        return {"commit_sha": res.commit_sha, "no_change": res.no_change,
                "changes": [c.to_manifest_entry() for c in batch.changes]}

    # ── shared result shaper ──────────────────────────────────────────
    def _result(self, res, rid: str | None, rel: str) -> dict:
        raw = self._read_bytes(rel)
        out = {
            "resource_id": rid,
            "virtual_path": rel,
            "commit_sha": res.commit_sha,
            "no_change": res.no_change,
        }
        if raw is not None:
            desc = vfs.describe(resource_id=rid or "", virtual_path=rel,
                                content=raw, snapshot_commit=res.commit_sha)
            out["resource_revision"] = desc.resource_revision
            out["content_revision"] = desc.content_revision
            out["content_hash"] = desc.content_hash
        return out
