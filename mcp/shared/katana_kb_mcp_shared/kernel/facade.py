"""Governed Full VFS façade (design §5.2, INV-5, INV-10).

``GovernedVFS`` implements the mechanical ``fs_*`` operation set on top of the
transaction engine, identity catalog and a domain policy. Domain tools and
``fs_*`` both compile into the SAME MutationBatch and flow through the SAME
policy → transaction pipeline — there is no raw bypass (INV-10): every mutation
is authorized, CAS-checked, projected, validated and Git-published, and the
identity catalog is committed atomically with the content it describes (INV-6).

Phase-1 scope: whole/range read, create (mints id), write (no implicit create),
exact-match edit, mkdir, copy (new id), rename (keeps id), delete (tombstone),
and single-repo ``fs_batch``. No file descriptors, seek, symlink or raw catalog
edit.
"""
from __future__ import annotations

import glob as _glob
import os

from . import paths, vfs
from .batch import Change, MutationBatch, Op
from .catalog import CATALOG_REL, Catalog
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
        """Validate + publish, folding any pending catalog change atomically.

        The identity catalog is canonical Git state (INV-6): if it changed while
        building this batch, its serialized bytes ride in the SAME commit. On
        any failure the in-memory catalog is rolled back so a rejected mutation
        leaves zero catalog delta visible (design §6.6).
        """
        if self.catalog.dirty:
            batch.add_reserved(CATALOG_REL, self.catalog.serialize())
        try:
            self.policy.validate(batch)
            res = self.engine.commit(batch, message=message)
        except Exception:
            self.catalog.reload()
            raise
        if res.no_change:
            self.catalog.reload()
        else:
            self.catalog.mark_clean()
        return res

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

    def fs_changes(self, *, since: str | None = None) -> dict:
        """Discovery of committed changes since a snapshot commit (design §5.2).

        Cursor binds an immutable snapshot commit; results never expose host
        paths. Returns the changed resource entries recorded in each manifest.
        """
        repo = self.engine.repo
        head = repo.head()
        commits = repo.first_parent_commits()
        changes: list[dict] = []
        for sha in commits:
            if since is not None and sha == since:
                break
            from .manifest import extract_from_message
            manifest = extract_from_message(repo.show_message(sha))
            if manifest is None:
                break
            for c in manifest.changes:
                changes.append({"commit": sha, **c})
        return {"snapshot_commit": head, "since": since, "changes": changes}

    def fs_capabilities(self) -> dict:
        """Protocol version + supported operations + freshness discovery (§5.1)."""
        return {
            "protocol_version": 1,
            "domain": self.engine.domain,
            "operations": sorted(FS_FACADE),
            "media_types": ["text/markdown", "text/plain",
                            "application/octet-stream"],
            "features": {"batch": True, "rename_keeps_id": True,
                         "copy_mints_id": True, "tombstone_on_delete": True},
        }

    def fs_status(self) -> dict:
        """Async push/projection freshness + checkpoint (design §6.5-6.8)."""
        return self.engine.status()

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
        before = self._read_bytes(rel)
        raw = content.encode("utf-8")
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              expected_base_commit=expected_base_commit)
        batch.add(Change(op=Op.WRITE, resource_id=rid or "", after_path=rel,
                         after_content=raw, before_content=before))
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
                         after_content=new_text.encode("utf-8"),
                         before_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_edit {rel}")
        return self._result(res, rid, rel)

    def fs_mkdir(self, *, virtual_path: str) -> dict:
        rel = paths.confine(virtual_path)
        batch = MutationBatch(domain=self.engine.domain)
        batch.add(Change(op=Op.MKDIR, resource_id="", after_path=rel,
                         after_content=b""))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_mkdir {rel}")
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
        # Rebind catalog IN the same transaction (INV-6): rename keeps the id.
        if rid:
            self.catalog.rebind(rid, dst)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.RENAME, resource_id=rid or "",
                         before_path=rel, after_path=dst))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_rename {rel} -> {dst}")
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
        # Tombstone IN the same transaction (INV-6): id never reused.
        if rid:
            self.catalog.tombstone(rid)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id)
        batch.add(Change(op=Op.DELETE, resource_id=rid or "", before_path=rel))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_delete {rel}")
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
            elif op is Op.RENAME and rid and rel:
                self.catalog.rebind(rid, rel)
            elif op is Op.DELETE and rid:
                self.catalog.tombstone(rid)
            batch.add(Change(op=op, resource_id=rid or "",
                             before_path=spec.get("from_path"),
                             after_path=rel, after_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_batch "
                                  f"{len(changes)} change(s)")
        return {"commit_sha": res.commit_sha, "no_change": res.no_change,
                "changes": [c.to_manifest_entry() for c in batch.changes]}

    # ── governed domain-tool entry (design §4.4, INV-5) ──────────────
    def commit_materialized(self, *, message: str,
                            writes: list[str] | None = None,
                            deletes: list[str] | None = None,
                            renames: list[tuple[str, str]] | None = None,
                            ids: dict | None = None,
                            tombstones: list[str] | None = None,
                            mutation_id: str | None = None,
                            request_hash: str | None = None,
                            expected_base_commit: str | None = None):
        """Publish a post-state a domain tool already wrote to the working tree.

        The 19 domain tools do their domain-specific planning/projection in
        ``store``/``_ingest``/``_lifecycle`` helpers, then hand the resulting
        working-tree paths here. This compiles them into ONE MutationBatch and
        runs the SAME policy → transaction/manifest/receipt/CAS pipeline as
        ``fs_*`` (design §4.4: "两组入口都编译为同一种 MutationBatch"). There is
        no independent domain write chain (INV-5). On any rejection the working
        tree and identity catalog are rolled back to the base commit, so a
        failed domain mutation leaves zero client-visible effect (design §6.6).

        ``writes``/``deletes`` are repo-relative paths currently present/absent
        on disk; ``renames`` are (old_rel, new_rel) pairs; ``ids`` maps a path
        to a caller-supplied resource_id (e.g. a Memory ``m-*`` card id) that is
        bound in the catalog atomically with the commit.
        """
        writes = list(writes or [])
        deletes = list(deletes or [])
        renames = list(renames or [])
        ids = dict(ids or {})
        base = self.engine.repo.head()
        touched: list[str] = []

        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=request_hash,
                              expected_base_commit=expected_base_commit,
                              already_materialized=True)

        for old_rel, new_rel in renames:
            raw = self._read_bytes(new_rel) or b""
            rid = ids.get(new_rel) or self.catalog.id_of(old_rel) or self.catalog.id_of(new_rel)
            before = self.engine.repo.read_blob_at(base, old_rel) if base else None
            if rid:
                self.catalog.rebind(rid, new_rel)
            batch.add(Change(op=Op.RENAME, resource_id=rid or "",
                             before_path=old_rel, after_path=new_rel,
                             after_content=raw, before_content=before))
            touched += [old_rel, new_rel]

        for rel in writes:
            raw = self._read_bytes(rel)
            if raw is None:
                continue
            rid = ids.get(rel) or self.catalog.id_of(rel)
            existed = base is not None and                 self.engine.repo.read_blob_at(base, rel) is not None
            if rid is None and not existed:
                rid = self.catalog.mint(rel)
            elif rid is not None:
                self.catalog.bind(rid, rel)
            op = Op.WRITE if existed else Op.CREATE
            before = self.engine.repo.read_blob_at(base, rel) if existed else None
            batch.add(Change(op=op, resource_id=rid or "", after_path=rel,
                             after_content=raw, before_content=before,
                             after_hash=vfs.identity.content_hash(raw)))
            touched.append(rel)

        for rel in deletes:
            rid = ids.get(rel) or self.catalog.id_of(rel)
            if rid:
                self.catalog.tombstone(rid)
            batch.add(Change(op=Op.DELETE, resource_id=rid or "",
                             before_path=rel))
            touched.append(rel)

        for tid in (tombstones or []):
            self.catalog.tombstone(tid)

        if self.catalog.dirty:
            batch.add_reserved(CATALOG_REL, self.catalog.serialize())

        try:
            self.policy.validate(batch)
            res = self.engine.commit(batch, message=message)
        except Exception:
            # Roll working tree + catalog back to base: zero visible effect.
            self.engine.repo.restore_paths(base, touched)
            self.engine.repo.sync_index()
            self.catalog.reload()
            raise
        if res.no_change:
            self.catalog.reload()
        else:
            self.catalog.mark_clean()
        return res

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


# The complete governed Full VFS operation set exposed by every app (design
# §5.2). Apps bind these one-to-one to MCP tools; the set is the parity anchor.
FS_FACADE = frozenset({
    "fs_capabilities", "fs_status", "fs_resolve", "fs_stat", "fs_list",
    "fs_glob", "fs_changes", "fs_read", "fs_create", "fs_write", "fs_edit",
    "fs_mkdir", "fs_copy", "fs_rename", "fs_delete", "fs_batch",
})
