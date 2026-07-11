"""Git-native transaction engine (design §6).

Deterministic mutation lifecycle applied to every governed write:

    resolve → CAS → project (writer-private staging) → validate → publish

Publish is the protected-ref compare-and-swap: before it fails there is zero
client-visible effect; after it succeeds the effect is durable and idempotent
replay of the same ``mutation_id + request_hash`` returns the original receipt.
An accepted request with no canonical delta returns NO_CHANGE.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from . import identity
from .batch import MutationBatch, Op
from .errors import (
    BASE_COMMIT_CONFLICT,
    IDEMPOTENCY_CONFLICT,
    KernelError,
    NO_CHANGE,
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
                 repository_epoch: int = 1) -> None:
        self.repo_root = repo_root
        self.domain = domain
        self.policy_version = policy_version
        self.repo = GitRepo(repo_root)
        self._configured_epoch = repository_epoch

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
    def _idempotency_check(self, batch: MutationBatch) -> dict | None:
        if not batch.mutation_id:
            return None
        prior = self._load_receipts().get(batch.mutation_id)
        if prior is None:
            return None
        if prior.get("request_hash") != batch.request_hash:
            raise KernelError(
                IDEMPOTENCY_CONFLICT,
                f"mutation_id {batch.mutation_id} already committed with a "
                "different request hash",
            )
        return prior["receipt"]

    # ── the publish pipeline ──────────────────────────────────────────
    def commit(self, batch: MutationBatch, *, message: str,
               materialize: bool = True) -> TransactionResult:
        self.repo.ensure_repo()

        epoch = self.repository_epoch()

        # Idempotency: identical mutation_id + request_hash returns the original
        # committed receipt (lost-response replay); same id, different hash is a
        # conflict (design 6.3).
        replay = self._idempotency_check(batch)
        if replay is not None:
            return TransactionResult(True, replay.get("commit_sha", ""),
                                     replay, no_change=False)

        # CAS on the canonical ref base.
        base = self.repo.head()
        if batch.expected_base_commit is not None and base is not None \
                and batch.expected_base_commit != base:
            raise KernelError(
                BASE_COMMIT_CONFLICT,
                "expected base commit no longer matches canonical head",
                current_commit=base, repository_epoch=epoch,
            )

        if batch.is_empty:
            # Accepted no-op: no commit, no manifest, no outbox event.
            receipt = {
                "mutation_id": batch.mutation_id,
                "code": NO_CHANGE,
                "repository_epoch": epoch,
                "commit_sha": base or "",
            }
            return TransactionResult(False, base or "", receipt, no_change=True)

        # Project: apply staged content to the working tree. When the caller
        # already materialised the post-state under the single-writer fence
        # (domain store path) it passes materialize=False and the engine only
        # publishes; either way the SAME manifest + CAS publish pipeline runs.
        if materialize:
            self._project(batch)

        # Build manifest (validation already done by policy before commit()).
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

        # Publish: single-repo compare-and-swap commit.
        paths = batch.touched_paths()
        try:
            new_sha = self.repo.commit_worktree(
                full_message, expected_base=base, paths=paths or None)
        except KernelError as e:
            if e.code == "COMMIT_FAILED" and "advanced" in e.message:
                raise KernelError(BASE_COMMIT_CONFLICT, e.message,
                                  current_commit=e.current_commit,
                                  repository_epoch=epoch) from e
            raise

        if not new_sha or new_sha == base:
            receipt = {
                "mutation_id": batch.mutation_id,
                "code": NO_CHANGE,
                "repository_epoch": epoch,
                "commit_sha": base or "",
            }
            return TransactionResult(False, base or "", receipt, no_change=True)

        receipt = build_receipt(manifest, new_sha)
        if batch.mutation_id:
            self._store_receipt(batch.mutation_id, receipt, batch.request_hash)
            # Persist the receipt mirror in the same canonical commit is not
            # required; it is a rebuildable operational mirror committed lazily.
        return TransactionResult(True, new_sha, receipt)

    def _project(self, batch: MutationBatch) -> None:
        """Materialise the batch into writer-private working-tree state."""
        for c in batch.changes:
            if c.op is Op.DELETE:
                if c.before_path:
                    target = os.path.join(self.repo_root, c.before_path)
                    if os.path.exists(target):
                        os.remove(target)
                continue
            if c.op is Op.MKDIR:
                if c.after_path:
                    os.makedirs(os.path.join(self.repo_root, c.after_path),
                                exist_ok=True)
                continue
            if c.op is Op.RENAME and c.before_path and c.after_path \
                    and c.after_content is None:
                src = os.path.join(self.repo_root, c.before_path)
                dst = os.path.join(self.repo_root, c.after_path)
                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                if os.path.exists(src):
                    os.replace(src, dst)
                continue
            # create/write/edit/copy or rename-with-content: write after_content.
            if c.after_path is not None and c.after_content is not None:
                if c.op is Op.RENAME and c.before_path \
                        and c.before_path != c.after_path:
                    old = os.path.join(self.repo_root, c.before_path)
                    if os.path.exists(old):
                        os.remove(old)
                dst = os.path.join(self.repo_root, c.after_path)
                os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
                with open(dst, "wb") as f:
                    f.write(c.after_content)

    # ── startup reconciliation (design §6.1, §6.6) ────────────────────
    def reconcile(self) -> dict:
        """Forward-recover operational mirrors from first-parent manifests.

        The walk stops at the first commit lacking a manifest (treated as the
        pre-genesis / imported baseline, design 6.1).
        """
        self.repo.ensure_repo()
        if not self.repo.has_commits():
            return {"reconciled": 0, "head": None}
        rebuilt = 0
        for sha in self.repo.first_parent_commits():
            msg = self.repo.show_message(sha)
            manifest = extract_from_message(msg)
            if manifest is None:
                # Pre-genesis imported commits are allowed to lack a manifest;
                # we stop the walk at the first such commit (baseline).
                break
            if manifest.repository_epoch != self.repository_epoch():
                raise KernelError(
                    REPOSITORY_EPOCH_CHANGED,
                    "manifest epoch differs from configured lineage",
                    repository_epoch=self.repository_epoch(),
                )
            if manifest.mutation_id:
                receipt = build_receipt(manifest, sha)
                self._store_receipt(manifest.mutation_id, receipt,
                                    manifest.request_hash)
                rebuilt += 1
        return {"reconciled": rebuilt, "head": self.repo.head()}
