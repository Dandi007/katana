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

import contextlib
import os
import shutil
import tempfile

from . import paths, vfs
from .batch import Change, MutationBatch, Op
from .catalog import CATALOG_REL, Catalog
from .errors import (
    INVALID_PATH,
    KernelError,
    NOT_FOUND,
    POLICY_VIOLATION,
    REF_MISMATCH,
    RESOURCE_REPLACED,
)
from .transaction import TransactionEngine


class _Staging:
    """A writer-private working directory seeded from a base commit."""

    def __init__(self, root: str, base: str | None) -> None:
        self.root = root
        self.base = base

    def abspath(self, rel: str) -> str:
        return paths.confined_join(self.root, rel)

    def read(self, rel: str) -> bytes | None:
        p = self.abspath(rel)
        if not os.path.isfile(p) or os.path.islink(p):
            return None
        with open(p, "rb") as f:
            return f.read()


def _glob_match(pattern: str, rel: str, fnmatch) -> bool:
    """Match a repo-relative path against a confined glob pattern.

    ``**`` matches across path separators (recursive); a single ``*`` matches
    within one segment only, mirroring POSIX-style globs but rooted inside the
    repo tree so nothing outside can ever match.
    """
    if "**" in pattern:
        # Translate ** to a cross-separator wildcard, * to within-segment.
        import re
        parts = []
        i = 0
        while i < len(pattern):
            if pattern[i:i + 2] == "**":
                parts.append(".*")
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    i += 1
            elif pattern[i] == "*":
                parts.append("[^/]*")
                i += 1
            elif pattern[i] == "?":
                parts.append("[^/]")
                i += 1
            else:
                parts.append(re.escape(pattern[i]))
                i += 1
        return re.fullmatch("".join(parts), rel) is not None
    if "/" in pattern:
        return fnmatch.fnmatch(rel, pattern) and rel.count("/") == pattern.count("/")
    # Bare pattern matches only top-level files (no separators).
    return "/" not in rel and fnmatch.fnmatch(rel, pattern)


class GovernedVFS:
    def __init__(self, engine: TransactionEngine, catalog: Catalog, policy) -> None:
        self.engine = engine
        self.catalog = catalog
        self.policy = policy
        self.repo_root = engine.repo_root

    def _refresh_catalog(self) -> None:
        """Reload the identity catalog from the canonical HEAD before minting.

        Multiple in-process ``GovernedVFS`` instances can share one repo (e.g.
        per-tenant Memory servers). Reading the committed ``.kb/catalog.json``
        from HEAD before each mutation means a stale in-memory instance never
        clobbers another's committed bindings; the ref CAS then serializes the
        actual publishes (operator P0 #5).
        """
        if self.catalog.dirty:
            return
        head = self.engine.repo.head()
        blob = self.engine.repo.read_blob_at(head, CATALOG_REL) if head else None
        self.catalog.load_canonical(blob)

    # ── helpers ───────────────────────────────────────────────────────
    def _replay(self, mutation_id: str | None, request_hash: str | None):
        """Return the original result for a replayed create, or None.

        Delegates idempotency detection to the engine (same id + same hash →
        original receipt; same id + different hash → IDEMPOTENCY_CONFLICT) and
        reshapes the stored receipt into the fs_create result envelope so a
        lost-response retry returns the same commit without a second effect.
        """
        if not mutation_id:
            return None
        receipt = self.engine.check_idempotent(mutation_id, request_hash)
        if receipt is None:
            return None
        return self._replay_result(receipt)

    def _replay_result(self, receipt: dict) -> dict:
        changes = receipt.get("changes") or [{}]
        first = changes[0] if changes else {}
        return {
            "resource_id": first.get("resource_id"),
            "virtual_path": first.get("after_path") or first.get("before_path"),
            "commit_sha": receipt.get("commit_sha"),
            "no_change": False,
        }

    def _check_expected(self, rel: str, raw: bytes | None, *,
                        expected_resource_revision: str | None = None,
                        expected_content_revision: str | None = None) -> None:
        if expected_resource_revision is None and expected_content_revision is None:
            return
        desc = self._descriptor(self.catalog.id_of(rel), rel, raw or b"").to_dict()
        if expected_resource_revision is not None and \
                desc["resource_revision"] != expected_resource_revision:
            raise KernelError(RESOURCE_REPLACED,
                              "expected_resource_revision does not match",
                              virtual_path=rel)
        if expected_content_revision is not None and \
                desc["content_revision"] != expected_content_revision:
            raise KernelError(RESOURCE_REPLACED,
                              "expected_content_revision does not match",
                              virtual_path=rel)

    def _auto_hash(self, op: str, rel: str, content: bytes) -> str:
        """Deterministic request hash for idempotency when the client omits one.

        A replayed request with the same op/path/payload maps to the same hash,
        so a lost-response retry returns the original committed receipt; a
        same-mutation_id request with a different payload trips
        IDEMPOTENCY_CONFLICT (design §6.2/§6.3, operator P1 #7).
        """
        return vfs.identity.request_hash(
            f"{self.engine.domain}\x00{op}\x00{rel}\x00"
            f"{vfs.identity.content_hash(content)}")

    def _canonical_or_mint(self, rel: str, content: bytes) -> str:
        """Adopt the domain-canonical id for content, else mint a fresh one.

        When the policy declares a canonical identity (e.g. a Memory card's
        frontmatter id), the catalog binds THAT id so identity never splits
        between the catalog and the source (operator P0 #6). Otherwise a fresh
        opaque id is minted.
        """
        canonical = None
        getter = getattr(self.policy, "canonical_id", None)
        if getter is not None:
            try:
                canonical = getter(rel, content)
            except Exception:
                canonical = None
        if canonical:
            self.catalog.bind(canonical, rel)
            return canonical
        return self.catalog.mint(rel)

    def _abs(self, rel: str) -> str:
        return os.path.join(self.repo_root, rel)

    def _snapshot(self) -> str | None:
        """The immutable commit reads/discovery are pinned to (design §6.1)."""
        return self.engine.repo.head()

    def _read_bytes(self, rel: str, snapshot: str | None = None) -> bytes | None:
        """Read canonical bytes from a pinned Git snapshot — never the working
        tree (design §5.2/§6.1). An out-of-band edit to a tracked file is
        therefore invisible; only committed content is client-visible. A path
        that is not a blob in the snapshot (missing, a tree, or a symlink) reads
        as absent, which also fails closed on symlink/host-path escape.
        """
        snap = snapshot if snapshot is not None else self._snapshot()
        if snap is None:
            return None
        if self.engine.repo.object_type_at(snap, rel) != "blob":
            return None
        return self.engine.repo.read_blob_at(snap, rel)

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

    def _descriptor(self, rid: str | None, rel: str, raw: bytes,
                    snapshot: str | None = None) -> vfs.NodeDescriptor:
        return vfs.describe(resource_id=rid or "", virtual_path=rel, content=raw,
                            snapshot_commit=snapshot if snapshot is not None
                            else self._snapshot())

    def _exists(self, rel: str, snapshot: str | None = None) -> bool:
        """Canonical existence in the pinned tree (blob or subtree)."""
        snap = snapshot if snapshot is not None else self._snapshot()
        if snap is None:
            return False
        return self.engine.repo.object_type_at(snap, rel) is not None

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
        snap = self._snapshot()
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        otype = self.engine.repo.object_type_at(snap, rel) if snap else None
        if otype == "tree" or (not rel):
            return vfs.describe(resource_id=rid or "", virtual_path=rel,
                                content=b"", snapshot_commit=snap,
                                node_type="dir").to_dict()
        raw = self._read_bytes(rel, snap)
        if raw is None:
            raise KernelError(NOT_FOUND, f"no such node: {rel}",
                              virtual_path=rel)
        return self._descriptor(rid, rel, raw, snap).to_dict()

    def fs_resolve(self, virtual_path: str) -> dict:
        rel = paths.confine(virtual_path)
        rid = self.catalog.id_of(rel)
        return {"resource_id": rid, "virtual_path": rel,
                "exists": self._exists(rel)}

    def fs_list(self, virtual_path: str = "") -> list[dict]:
        # Confine non-empty inputs: ordinary traffic may list the repo root
        # ("") but never a reserved namespace like .kb/.git (design §7.2).
        base = paths.confine(virtual_path) if virtual_path else ""
        snap = self._snapshot()
        if snap is None:
            return []
        if base and self.engine.repo.object_type_at(snap, base) != "tree":
            raise KernelError(NOT_FOUND, f"not a directory: {virtual_path}",
                              virtual_path=virtual_path)
        out: list[dict] = []
        for name, node_type in sorted(self.engine.repo.list_tree_at(snap, base)):
            rel = f"{base}/{name}" if base else name
            if paths.is_reserved(rel):
                continue
            if node_type == "dir":
                out.append(vfs.describe(
                    resource_id="", virtual_path=rel, content=b"",
                    snapshot_commit=snap, node_type="dir").to_dict())
            else:
                raw = self._read_bytes(rel, snap) or b""
                out.append(self._descriptor(self.catalog.id_of(rel), rel,
                                            raw, snap).to_dict())
        return out

    def fs_glob(self, pattern: str) -> list[dict]:
        """Confined glob over the canonical tree (design §5.2, §7.2).

        The pattern is validated as a safe repo-relative glob (no absolute
        path, ``..``, backslash, NUL or reserved prefix) and matched against the
        blob paths of the pinned snapshot — never the host filesystem, so
        ``../*`` and host paths cannot leak. Returns uniform node descriptors.
        """
        self._check_glob_pattern(pattern)
        snap = self._snapshot()
        if snap is None:
            return []
        import fnmatch
        out: list[dict] = []
        for rel in sorted(self.engine.repo.list_tree_recursive(snap)):
            if paths.is_reserved(rel):
                continue
            if _glob_match(pattern, rel, fnmatch):
                raw = self._read_bytes(rel, snap) or b""
                out.append(self._descriptor(self.catalog.id_of(rel), rel,
                                            raw, snap).to_dict())
        return out

    @staticmethod
    def _check_glob_pattern(pattern: str) -> None:
        if not isinstance(pattern, str) or pattern == "":
            raise KernelError(INVALID_PATH, "empty glob pattern")
        if "\x00" in pattern:
            raise KernelError(INVALID_PATH, "NUL byte in glob pattern")
        if "\\" in pattern:
            raise KernelError(INVALID_PATH, "backslash in glob pattern")
        if pattern.startswith("/"):
            raise KernelError(INVALID_PATH, "absolute glob pattern")
        for seg in pattern.split("/"):
            if seg == "..":
                raise KernelError(INVALID_PATH, "parent traversal in glob")
        top = pattern.split("/", 1)[0].split("*", 1)[0]
        if top in paths.RESERVED_PREFIXES:
            raise KernelError(INVALID_PATH, "reserved namespace glob")

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
                  mutation_id: str | None = None,
                  request_hash: str | None = None) -> dict:
        self._refresh_catalog()
        rel = paths.confine(virtual_path)
        raw0 = content.encode("utf-8")
        rh = request_hash or self._auto_hash("create", rel, raw0)
        # Idempotent replay short-circuits BEFORE stateful pre-checks (design
        # §6.3): a lost-response retry must not fail on "path exists" now that
        # the original create already committed the file.
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return replay
        if self._exists(rel):
            raise KernelError(POLICY_VIOLATION, f"path exists: {rel}",
                              virtual_path=rel)
        raw = raw0
        rid = self._canonical_or_mint(rel, raw)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh)
        batch.add(Change(op=Op.CREATE, resource_id=rid, after_path=rel,
                         after_content=raw, after_hash=vfs.identity.content_hash(raw)))
        res = self._commit(batch, f"feat({self.engine.domain}): fs_create {rel} ({rid})")
        return self._result(res, rid, rel)

    def fs_write(self, *, resource_id: str | None = None,
                 virtual_path: str | None = None, content: str,
                 expected_base_commit: str | None = None,
                 mutation_id: str | None = None,
                 request_hash: str | None = None,
                 expected_resource_revision: str | None = None,
                 expected_content_revision: str | None = None) -> dict:
        self._refresh_catalog()
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        raw = content.encode("utf-8")
        rh = request_hash or self._auto_hash("write", rel, raw)
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return replay
        if not self._exists(rel):
            raise KernelError(NOT_FOUND,
                              "fs_write does not implicitly create; use fs_create",
                              virtual_path=rel)
        before = self._read_bytes(rel)
        self._check_expected(rel, before,
                             expected_resource_revision=expected_resource_revision,
                             expected_content_revision=expected_content_revision)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh,
                              expected_base_commit=expected_base_commit)
        batch.add(Change(op=Op.WRITE, resource_id=rid or "", after_path=rel,
                         after_content=raw, before_content=before))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_write {rel}")
        return self._result(res, rid, rel)

    def fs_edit(self, *, resource_id: str | None = None,
                virtual_path: str | None = None, old_string: str,
                new_string: str, replace_all: bool = False,
                mutation_id: str | None = None,
                request_hash: str | None = None,
                expected_resource_revision: str | None = None,
                expected_content_revision: str | None = None) -> dict:
        self._refresh_catalog()
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        import json as _json
        rh = request_hash or self._auto_hash(
            "edit", rel, _json.dumps([old_string, new_string, replace_all],
                                     ensure_ascii=False).encode("utf-8"))
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return replay
        raw = self._read_bytes(rel)
        self._check_expected(rel, raw,
                             expected_resource_revision=expected_resource_revision,
                             expected_content_revision=expected_content_revision)
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
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh)
        batch.add(Change(op=Op.EDIT, resource_id=rid or "", after_path=rel,
                         after_content=new_text.encode("utf-8"),
                         before_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_edit {rel}")
        return self._result(res, rid, rel)

    def fs_mkdir(self, *, virtual_path: str, mutation_id: str | None = None,
                 request_hash: str | None = None) -> dict:
        self._refresh_catalog()
        rel = paths.confine(virtual_path)
        rh = request_hash or self._auto_hash("mkdir", rel, b"")
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return replay
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh)
        batch.add(Change(op=Op.MKDIR, resource_id="", after_path=rel,
                         after_content=b""))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_mkdir {rel}")
        return {"virtual_path": rel, "commit_sha": res.commit_sha}

    # ── structure ─────────────────────────────────────────────────────
    def fs_rename(self, *, resource_id: str | None = None,
                  virtual_path: str | None = None, new_path: str,
                  mutation_id: str | None = None,
                  request_hash: str | None = None,
                  expected_resource_revision: str | None = None,
                  expected_content_revision: str | None = None) -> dict:
        self._refresh_catalog()
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        dst = paths.confine(new_path)
        rh = request_hash or self._auto_hash("rename", rel, dst.encode("utf-8"))
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return replay
        before = self._read_bytes(rel)
        self._check_expected(rel, before,
                             expected_resource_revision=expected_resource_revision,
                             expected_content_revision=expected_content_revision)
        if self._exists(dst):
            raise KernelError(POLICY_VIOLATION, f"destination exists: {dst}",
                              virtual_path=dst)
        # Rebind catalog IN the same transaction (INV-6): rename keeps the id.
        if rid:
            self.catalog.rebind(rid, dst)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh)
        batch.add(Change(op=Op.RENAME, resource_id=rid or "",
                         before_path=rel, after_path=dst))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_rename {rel} -> {dst}")
        return self._result(res, rid, dst)

    def fs_copy(self, *, resource_id: str | None = None,
                virtual_path: str | None = None, new_path: str,
                mutation_id: str | None = None,
                request_hash: str | None = None,
                expected_resource_revision: str | None = None,
                expected_content_revision: str | None = None) -> dict:
        self._refresh_catalog()
        rel, _ = self._resolve_target(resource_id=resource_id,
                                      virtual_path=virtual_path)
        dst = paths.confine(new_path)
        rh = request_hash or self._auto_hash("copy", rel, dst.encode("utf-8"))
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return replay
        raw = self._read_bytes(rel)
        if raw is None:
            raise KernelError(NOT_FOUND, f"no such file: {rel}", virtual_path=rel)
        self._check_expected(rel, raw,
                             expected_resource_revision=expected_resource_revision,
                             expected_content_revision=expected_content_revision)
        if self._exists(dst):
            raise KernelError(POLICY_VIOLATION, f"destination exists: {dst}",
                              virtual_path=dst)
        raw = self._copy_content_for_new_identity(dst, raw)
        new_rid = self._canonical_or_mint(dst, raw)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh)
        batch.add(Change(op=Op.COPY, resource_id=new_rid, after_path=dst,
                         after_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_copy {rel} -> {dst}")
        return self._result(res, new_rid, dst)

    def fs_delete(self, *, resource_id: str | None = None,
                  virtual_path: str | None = None,
                  mutation_id: str | None = None,
                  request_hash: str | None = None,
                  expected_resource_revision: str | None = None,
                  expected_content_revision: str | None = None) -> dict:
        self._refresh_catalog()
        rel, rid = self._resolve_target(resource_id=resource_id,
                                        virtual_path=virtual_path)
        rh = request_hash or self._auto_hash("delete", rel, b"")
        replay = self._replay(mutation_id, rh)
        if replay is not None:
            return {**replay, "deleted": True}
        before = self._read_bytes(rel)
        self._check_expected(rel, before,
                             expected_resource_revision=expected_resource_revision,
                             expected_content_revision=expected_content_revision)
        if not self._exists(rel):
            raise KernelError(NOT_FOUND, f"no such file: {rel}", virtual_path=rel)
        # Tombstone IN the same transaction (INV-6): id never reused.
        if rid:
            self.catalog.tombstone(rid)
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh)
        batch.add(Change(op=Op.DELETE, resource_id=rid or "", before_path=rel))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_delete {rel}")
        return {"resource_id": rid, "virtual_path": rel,
                "deleted": True, "commit_sha": res.commit_sha}

    # ── batch ─────────────────────────────────────────────────────────
    def fs_batch(self, changes: list[dict], *,
                 expected_base_commit: str | None = None,
                 mutation_id: str | None = None,
                 request_hash: str | None = None) -> dict:
        self._refresh_catalog()
        import json as _json
        auto = self._auto_hash("batch", "",
                               _json.dumps(changes, sort_keys=True,
                                           ensure_ascii=False).encode("utf-8"))
        rh = request_hash or auto
        receipt = self.engine.check_idempotent(mutation_id, rh) if mutation_id else None
        if receipt is not None:
            return {"commit_sha": receipt.get("commit_sha"), "no_change": False,
                    "changes": receipt.get("changes") or []}
        batch = MutationBatch(domain=self.engine.domain, mutation_id=mutation_id,
                              request_hash=rh,
                              expected_base_commit=expected_base_commit)
        for spec in changes:
            op = Op(spec["op"])
            rel = paths.confine(spec["virtual_path"]) if spec.get("virtual_path") else None
            # Confine from_path too (operator P0 #1): an unconfined client
            # from_path could otherwise be materialized/deleted outside the repo
            # (``../../host-sentinel``). Reserved namespaces and traversal are
            # rejected here, before any planning or materialize.
            from_path = paths.confine(spec["from_path"]) \
                if spec.get("from_path") else None
            rid = spec.get("resource_id")
            content = spec.get("content")
            raw = content.encode("utf-8") if content is not None else None
            if op is Op.COPY:
                src = from_path or (paths.confine(spec["source_path"])
                                    if spec.get("source_path") else None)
                if raw is None and src:
                    raw = self._read_bytes(src)
                if raw is not None and rel:
                    raw = self._copy_content_for_new_identity(rel, raw)
            if op in (Op.CREATE, Op.COPY) and rel and not rid:
                rid = self._canonical_or_mint(rel, raw or b"")
            elif op is Op.RENAME and rid and rel:
                self.catalog.rebind(rid, rel)
            elif op is Op.DELETE and rid:
                self.catalog.tombstone(rid)
            before_path = from_path
            after_path = rel
            if op is Op.DELETE and before_path is None:
                before_path = rel
                after_path = None
            batch.add(Change(op=op, resource_id=rid or "",
                             before_path=before_path,
                             after_path=after_path, after_content=raw))
        res = self._commit(batch, f"chore({self.engine.domain}): fs_batch "
                                  f"{len(changes)} change(s)")
        return {"commit_sha": res.commit_sha, "no_change": res.no_change,
                "changes": [c.to_manifest_entry() for c in batch.changes]}


    def _copy_content_for_new_identity(self, dst: str, raw: bytes) -> bytes:
        """Let a domain policy rewrite copied content to a fresh canonical id."""
        hook = getattr(self.policy, "prepare_copy", None)
        if hook is None:
            return raw
        try:
            return hook(dst, raw)
        except TypeError:
            return raw

    # ── writer-private staging for domain tools (design §5.5 step 5) ──
    @contextlib.contextmanager
    def staging(self):
        """Yield a writer-private working directory seeded from HEAD.

        Domain tools (Memory store, Wiki ingest, WF lifecycle) run their
        projection against this private copy instead of the real canonical
        working tree, so a mid-projection failure or a rejected policy check
        leaves ZERO client-visible effect and never dirties the canonical tree
        (operator P0 #2; design §6.6). The caller then hands the resulting
        relative paths to :meth:`commit_staged`, which reads the projected bytes
        from the staging dir and publishes them via the same CAS pipeline.
        """
        base = self._snapshot()
        d = tempfile.mkdtemp(prefix="kb-staging-")
        try:
            self.engine.repo.export_head_to(d)
            yield _Staging(d, base)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def commit_staged(self, staging, *, message: str,
                      writes: list[str] | None = None,
                      deletes: list[str] | None = None,
                      renames: list[tuple[str, str]] | None = None,
                      ids: dict | None = None,
                      tombstones: list[str] | None = None,
                      mutation_id: str | None = None,
                      request_hash: str | None = None,
                      expected_base_commit: str | None = None):
        """Publish a post-state projected into writer-private staging.

        Reads the projected bytes from the staging dir (never the canonical
        working tree), compiles them into ONE MutationBatch and runs the SAME
        policy → transaction/manifest/receipt/CAS pipeline as ``fs_*`` (design
        §4.4, INV-5). The canonical working tree is only written by the engine's
        post-publish materialize step, so nothing is visible before the ref CAS
        succeeds and a rejection needs no working-tree rollback.
        """
        writes = list(writes or [])
        deletes = list(deletes or [])
        renames = list(renames or [])
        ids = dict(ids or {})
        base = staging.base
        self._refresh_catalog()

        batch = MutationBatch(
            domain=self.engine.domain, mutation_id=mutation_id,
            request_hash=request_hash,
            expected_base_commit=expected_base_commit
            if expected_base_commit is not None else base)

        writes = [paths.confine(p) for p in writes]
        deletes = [paths.confine(p) for p in deletes]
        renames = [(paths.confine(a), paths.confine(b)) for a, b in renames]

        for old_rel, new_rel in renames:
            raw = staging.read(new_rel) or b""
            rid = (ids.get(new_rel) or self.catalog.id_of(old_rel)
                   or self.catalog.id_of(new_rel))
            before = self.engine.repo.read_blob_at(base, old_rel) if base else None
            if rid:
                self.catalog.rebind(rid, new_rel)
            batch.add(Change(op=Op.RENAME, resource_id=rid or "",
                             before_path=old_rel, after_path=new_rel,
                             after_content=raw, before_content=before,
                             after_hash=vfs.identity.content_hash(raw)))

        for rel in writes:
            raw = staging.read(rel)
            if raw is None:
                continue
            rid = ids.get(rel) or self.catalog.id_of(rel)
            existed = base is not None and \
                self.engine.repo.object_type_at(base, rel) == "blob"
            if rid is None and not existed:
                rid = self.catalog.mint(rel)
            elif rid is not None:
                self.catalog.bind(rid, rel)
            op = Op.WRITE if existed else Op.CREATE
            before = self.engine.repo.read_blob_at(base, rel) if existed else None
            batch.add(Change(op=op, resource_id=rid or "", after_path=rel,
                             after_content=raw, before_content=before,
                             after_hash=vfs.identity.content_hash(raw)))

        for rel in deletes:
            rid = ids.get(rel) or self.catalog.id_of(rel)
            if rid:
                self.catalog.tombstone(rid)
            batch.add(Change(op=Op.DELETE, resource_id=rid or "",
                             before_path=rel))

        for tid in (tombstones or []):
            self.catalog.tombstone(tid)

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
