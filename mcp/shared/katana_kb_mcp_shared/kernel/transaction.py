"""Git-native transaction engine (design §6).

Deterministic mutation lifecycle applied to every governed write:

    resolve → CAS → project (writer-private staging) → validate → publish

Both façades — the 19 domain tools and the generic ``fs_*`` set — compile into a
single :class:`MutationBatch` and flow through THIS engine; neither implements
its own write chain (design §4.4, INV-5). Publish is the protected-ref
compare-and-swap: before it succeeds there is zero client-visible effect; after
it succeeds the effect is durable and idempotent replay of the same
``mutation_id + request_hash`` returns the original receipt. Identity/link
catalog changes ride in the SAME commit as the content they describe (INV-6).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .batch import MutationBatch, Op
from .errors import (
    BASE_COMMIT_CONFLICT,
    IDEMPOTENCY_CONFLICT,
    KernelError,
    NO_CHANGE,
    RECOVERY_REQUIRED,
    REPOSITORY_EPOCH_CHANGED,
)
from .gitrepo import GitRepo
from .manifest import (
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    Manifest,
    build_receipt,
    encode_trailer,
    extract_from_message,
)

# Reserved on-disk locations owned by the server (hidden from fs_*).
_KB_DIR = ".kb"
_EPOCH_FILE = os.path.join(_KB_DIR, "epoch")
_RECEIPTS_FILE = os.path.join(_KB_DIR, "receipts.json")


@dataclass
class TransactionResult:
    committed: bool
    commit_sha: str
    receipt: dict
    no_change: bool = False


class TransactionEngine:
    """Owns the publish pipeline for exactly one data repo."""

    def __init__(self, repo_root: str, *, domain: str, policy_version: int = 1,
                 repository_epoch: int = 1, projection=None) -> None:
        self.repo_root = repo_root
        self.domain = domain
        self.policy_version = policy_version
        self.repo = GitRepo(repo_root)
        self._configured_epoch = repository_epoch
        # Observable async push/projection tracker (design §6.5-6.8). Injected
        # so tests can substitute a fake; defaults to a local file-backed one.
        self.projection = projection

    # ── operational mirrors (rebuildable from Git history) ────────────
    def _kb_path(self, rel: str) -> str:
        return os.path.join(self.repo_root, rel)

    def repository_epoch(self) -> int:
        path = self._kb_path(_EPOCH_FILE)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip() or self._configured_epoch)
        return self._configured_epoch

    def _load_receipts(self) -> dict:
        path = self._kb_path(_RECEIPTS_FILE)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _store_receipt(self, key: str, receipt: dict, request_hash: str | None) -> None:
        os.makedirs(self._kb_path(_KB_DIR), exist_ok=True)
        data = self._load_receipts()
        data[key] = {"request_hash": request_hash, "receipt": receipt}
        with open(self._kb_path(_RECEIPTS_FILE), "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, ensure_ascii=False)

    # ── idempotency ───────────────────────────────────────────────────
    def check_idempotent(self, mutation_id: str | None,
                         request_hash: str | None) -> dict | None:
        """Return a prior committed receipt for a replayed mutation, or None.

        Same id + same hash → original receipt (lost-response replay);
        same id + different hash → IDEMPOTENCY_CONFLICT (design §6.3).
        """
        if not mutation_id:
            return None
        prior = self._load_receipts().get(mutation_id)
        if prior is None:
            return None
        if prior.get("request_hash") != request_hash:
            raise KernelError(
                IDEMPOTENCY_CONFLICT,
                f"mutation_id {mutation_id} already committed with a "
                "different request hash",
            )
        return prior["receipt"]

    # ── plan a batch into concrete blob writes/deletes ────────────────
    def _plan_io(self, batch: MutationBatch, base: str | None
                 ) -> tuple[dict[str, bytes], list[str]]:
        writes: dict[str, bytes] = {}
        deletes: list[str] = []
        for c in batch.changes:
            if c.op is Op.DELETE:
                if c.before_path:
                    deletes.append(c.before_path)
                continue
            if c.op is Op.MKDIR:
                # Git does not track empty dirs; a keep marker makes it durable.
                path = (c.after_path or "").rstrip("/")
                if path:
                    writes[f"{path}/.gitkeep"] = c.after_content or b""
                continue
            if c.op is Op.RENAME and c.after_content is None and c.before_path \
                    and c.after_path:
                # Pure move: carry existing content forward, drop old path.
                data = self.repo.read_blob_at(base, c.before_path) if base else None
                if data is None:
                    data = self._read_worktree(c.before_path) or b""
                writes[c.after_path] = data
                if c.before_path != c.after_path:
                    deletes.append(c.before_path)
                continue
            if c.after_path is not None and c.after_content is not None:
                if c.op is Op.RENAME and c.before_path \
                        and c.before_path != c.after_path:
                    deletes.append(c.before_path)
                writes[c.after_path] = c.after_content
        # Reserved (catalog/link) files ride atomically in the same commit.
        for path, content in batch.reserved.items():
            if content is None:
                deletes.append(path)
            else:
                writes[path] = content
        # A delete followed by a write of the same path in one batch is a write.
        deletes = [d for d in deletes if d not in writes]
        return writes, deletes

    def _read_worktree(self, rel: str) -> bytes | None:
        p = self._kb_path(rel)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    # ── the publish pipeline ──────────────────────────────────────────
    def commit(self, batch: MutationBatch, *, message: str,
               materialize: bool = True) -> TransactionResult:
        self.repo.ensure_repo()
        epoch = self.repository_epoch()

        replay = self.check_idempotent(batch.mutation_id, batch.request_hash)
        if replay is not None:
            return TransactionResult(True, replay.get("commit_sha", ""),
                                     replay, no_change=False)

        base = self.repo.head()

        # Unknown dirty tracked working tree/index is fail-stop (design §6.6),
        # unless the caller deliberately projected the post-state itself.
        if not batch.already_materialized and self.repo.is_dirty():
            raise KernelError(
                RECOVERY_REQUIRED,
                "canonical working tree/index is dirty; refusing to publish",
                current_commit=base, repository_epoch=epoch)

        if batch.expected_base_commit is not None and base is not None \
                and batch.expected_base_commit != base:
            raise KernelError(
                BASE_COMMIT_CONFLICT,
                "expected base commit no longer matches canonical head",
                current_commit=base, repository_epoch=epoch)

        if batch.is_empty and not batch.reserved:
            return self._no_change(batch, base, epoch)

        writes, deletes = self._plan_io(batch, base)

        manifest = Manifest(
            protocol_version=PROTOCOL_VERSION,
            schema_version=SCHEMA_VERSION,
            policy_version=self.policy_version,
            repository_epoch=epoch,
            domain=self.domain,
            tenant=batch.tenant,
            principal_id=batch.principal_id,
            scopes=list(batch.scopes),
            mutation_id=batch.mutation_id,
            request_hash=batch.request_hash,
            base_commit=base,
            changes=[c.to_manifest_entry() for c in batch.changes],
            warnings=list(batch.warnings),
            projection_events=list(batch.projection_events),
        )
        full_message = f"{message}\n\n{encode_trailer(manifest)}"

        # Publish: writer-private staging + protected-ref CAS. On any failure
        # here the ref and (materialized) working tree are rolled back so a
        # partial batch is never client-visible (design §6.6).
        try:
            new_sha = self.repo.publish(
                expected_base=base, message=full_message,
                writes=writes, deletes=deletes)
        except KernelError as e:
            if batch.already_materialized:
                self.repo.restore_paths(base, list(writes) + deletes)
            if e.code == "COMMIT_FAILED" and "advanced" in e.message:
                raise KernelError(BASE_COMMIT_CONFLICT, e.message,
                                  current_commit=e.current_commit,
                                  repository_epoch=epoch) from e
            raise

        if not new_sha or new_sha == base:
            return self._no_change(batch, base, epoch)

        # Reflect committed post-state into the working tree so canonical reads
        # (which read the tree) match. Safe to always run: identical bytes when
        # already materialized, plus the reserved catalog file either way.
        if materialize:
            self.repo.materialize(writes=writes, deletes=deletes)
            self.repo.sync_index()

        projection_status = self._enqueue_projection(new_sha, manifest)
        receipt = build_receipt(manifest, new_sha,
                                sync_status=self._sync_status(),
                                projection_status=projection_status)
        if batch.mutation_id:
            self._store_receipt(batch.mutation_id, receipt, batch.request_hash)
        return TransactionResult(True, new_sha, receipt)

    def _no_change(self, batch, base, epoch) -> TransactionResult:
        receipt = {
            "mutation_id": batch.mutation_id,
            "code": NO_CHANGE,
            "repository_epoch": epoch,
            "commit_sha": base or "",
        }
        return TransactionResult(False, base or "", receipt, no_change=True)

    # ── observable async push/projection (design §6.5-6.8) ────────────
    def _tracker(self):
        if self.projection is None:
            from .projection import ProjectionTracker
            self.projection = ProjectionTracker(self.repo_root)
        return self.projection

    def _enqueue_projection(self, commit_sha: str, manifest: Manifest) -> dict:
        try:
            return self._tracker().record_commit(commit_sha, manifest)
        except Exception:  # pragma: no cover - observability must not block ACK
            return {}

    def _sync_status(self) -> str:
        try:
            return self._tracker().sync_status()
        except Exception:  # pragma: no cover
            return "pending"

    def status(self) -> dict:
        """Freshness/checkpoint snapshot for operate-scope status tools."""
        return self._tracker().status(self.repo.head())

    # ── startup reconciliation (design §6.1, §6.6) ────────────────────
    def reconcile(self) -> dict:
        """Forward-recover operational mirrors from first-parent manifests.

        The walk stops at the first commit lacking a manifest (treated as the
        pre-genesis / imported baseline, design §6.1).
        """
        self.repo.ensure_repo()
        if not self.repo.has_commits():
            return {"reconciled": 0, "head": None}
        rebuilt = 0
        for sha in self.repo.first_parent_commits():
            msg = self.repo.show_message(sha)
            manifest = extract_from_message(msg)
            if manifest is None:
                break
            if manifest.repository_epoch != self.repository_epoch():
                raise KernelError(
                    REPOSITORY_EPOCH_CHANGED,
                    "manifest epoch differs from configured lineage",
                    repository_epoch=self.repository_epoch())
            if manifest.mutation_id:
                receipt = build_receipt(manifest, sha)
                self._store_receipt(manifest.mutation_id, receipt,
                                    manifest.request_hash)
                rebuilt += 1
        return {"reconciled": rebuilt, "head": self.repo.head()}
