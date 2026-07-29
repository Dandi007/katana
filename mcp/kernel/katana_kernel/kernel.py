"""GovernedKernel: single authoritative-writer binding + unique mutate entry point.

Orchestrates the full governance chain:
  authorize -> CAS(expected_base_sha) -> policy -> VFS -> ledger -> manifest -> git commit
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from collections.abc import Callable
from typing import Any

from katana_kernel.gitops import (
    CASRejectionError,
    FileImage,
    RollbackSafetyError,
    TransactionJournal,
    amend_commit,
    cas_guard,
    commit_changed_paths,
    commit_file_image,
    commit_is_ancestor,
    commit_parents,
    find_commit_with_trailer,
    git_commit,
    head_sha,
    is_working_tree_clean,
    read_katana_commit_trailers,
    repository_mutation_lock,
    require_clean_working_tree,
    validate_runtime_state_paths,
    validate_runtime_state_tree,
    validate_transaction_paths,
)
from katana_kernel.idempotency import (
    MutationRecord,
    canonical_request_hash,
)


_RECEIPT_PROTOCOL = "katana-idempotency-v1"
_RESERVED_TRAILER_PREFIX = "Katana-"
_VERIFIED_HEAD_META = "verified_head"


@dataclasses.dataclass
class DomainBinding:
    domain: str
    policy: "DomainPolicy"
    vfs: "GovernedVFS"
    ledger: "ResourceIdLedger"
    manifest: "TransactionManifest"
    repo_root: str
    mutation_ledger: "SQLiteMutationLedger | None" = None


class MutationBrokenError(RuntimeError):
    """Raised when a failed mutation requires fail-stop manual recovery."""

    def __init__(self, detail: str, rollback: dict[str, Any]):
        super().__init__(detail)
        self.rollback = rollback

    def as_error(self, **context: Any) -> dict[str, Any]:
        """Return the stable machine-readable domain error envelope."""
        return {
            "code": "BROKEN",
            "state": "BROKEN",
            "message": str(self),
            "retryable": False,
            "blocked": True,
            "manual_recovery_required": True,
            "rollback": self.rollback,
            **{key: value for key, value in context.items() if value is not None},
        }


class GovernedKernel:
    def __init__(self):
        self._bindings: dict[str, DomainBinding] = {}
        self._repo_roots: set[str] = set()

    def bind(
        self,
        domain: str,
        policy: "DomainPolicy",
        vfs: "GovernedVFS",
        ledger: "ResourceIdLedger",
        manifest: "TransactionManifest",
        repo_root: str,
        *,
        mutation_ledger: "SQLiteMutationLedger | None" = None,
    ) -> DomainBinding:
        if domain in self._bindings:
            raise ValueError(f"domain {domain!r} already bound")
        if mutation_ledger is not None and manifest.git_tracked:
            raise ValueError(
                "SQLite mutation ledger requires a runtime, non-Git manifest"
            )
        resolved = os.path.realpath(repo_root)
        if resolved in self._repo_roots:
            raise ValueError(f"repo_root {resolved!r} already bound by another domain")
        binding = DomainBinding(
            domain=domain,
            policy=policy,
            vfs=vfs,
            ledger=ledger,
            manifest=manifest,
            repo_root=repo_root,
            mutation_ledger=mutation_ledger,
        )
        self._bindings[domain] = binding
        self._repo_roots.add(resolved)
        return binding

    def get_binding(self, domain: str) -> DomainBinding:
        if domain not in self._bindings:
            raise ValueError(f"domain {domain!r} not bound")
        return self._bindings[domain]

    @staticmethod
    def _validate_runtime_configuration(binding: DomainBinding) -> None:
        if binding.manifest.git_tracked:
            manifest_probe = os.path.join(
                binding.manifest.manifests_dir, ".path-probe",
            )
            validate_transaction_paths(binding.repo_root, [manifest_probe])
            return

        validate_runtime_state_tree(
            binding.repo_root, binding.manifest.manifests_dir,
        )
        if binding.mutation_ledger is not None:
            ledger_path = binding.mutation_ledger.path
            validate_runtime_state_paths(
                binding.repo_root,
                [
                    ledger_path,
                    f"{ledger_path}-wal",
                    f"{ledger_path}-shm",
                    f"{ledger_path}-journal",
                ],
            )

    @staticmethod
    def _request_hash(domain: str, op: str, payload: Any) -> str:
        return canonical_request_hash(
            {
                "schema_version": 1,
                "domain": domain,
                "op": op,
                "payload": payload,
            }
        )

    @staticmethod
    def _validate_commit_message(commit_msg: str | None) -> None:
        for line in (commit_msg or "").splitlines():
            if line.startswith(_RESERVED_TRAILER_PREFIX):
                raise ValueError(
                    "commit message contains a reserved Katana trailer"
                )

    @staticmethod
    def _postimage_hash(image: FileImage) -> str:
        digest = hashlib.sha256()
        digest.update(b"1" if image.exists else b"0")
        git_mode = 0o755 if image.mode & 0o111 else 0o644
        digest.update(str(git_mode).encode("ascii"))
        digest.update(b"\0")
        digest.update(image.data)
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _receipt_commit_message(
        base_message: str,
        record: MutationRecord,
        result_hash: str,
    ) -> str:
        trailers = (
            f"Katana-Protocol: {_RECEIPT_PROTOCOL}",
            f"Katana-Domain: {record.domain}",
            f"Katana-Operation: {record.op}",
            f"Katana-Mutation-Id: {record.mutation_id}",
            f"Katana-Idempotency-Key-SHA256: {record.key_hash}",
            f"Katana-Request-SHA256: {record.request_hash}",
            f"Katana-Result-SHA256: {result_hash}",
            "Katana-Postimages-SHA256: "
            f"{canonical_request_hash(record.postimages)}",
        )
        return base_message.rstrip() + "\n\n" + "\n".join(trailers)

    @staticmethod
    def _prepared_response(
        record: MutationRecord,
        commit_sha: str,
    ) -> dict[str, Any]:
        if not isinstance(record.result, dict):
            raise RollbackSafetyError(
                f"prepared mutation has no durable result: {record.mutation_id}"
            )
        operation_result = record.result.get("operation_result")
        manifest_id = record.result.get("manifest_id")
        if not isinstance(operation_result, dict) or not manifest_id:
            raise RollbackSafetyError(
                f"prepared mutation result is incomplete: {record.mutation_id}"
            )
        return {
            **operation_result,
            "git": {"committed": True, "detail": commit_sha},
            "manifest": {"manifest_id": manifest_id},
            "mutation_id": record.mutation_id,
        }

    @staticmethod
    def _unresolved_error(record: MutationRecord) -> MutationBrokenError:
        return MutationBrokenError(
            f"unresolved governed mutation blocks writes: {record.mutation_id}",
            {
                "state": "BROKEN",
                "ledger_state": record.state,
                "mutation_id": record.mutation_id,
                "paths": record.changed_paths,
                "detail": record.error
                or "runtime mutation requires reconciliation",
            },
        )

    def _validate_receipt(
        self,
        binding: DomainBinding,
        record: MutationRecord,
        commit_sha: str,
    ) -> None:
        trailers = read_katana_commit_trailers(
            binding.repo_root, commit_sha,
        )
        expected = {
            "Katana-Protocol": _RECEIPT_PROTOCOL,
            "Katana-Domain": record.domain,
            "Katana-Operation": record.op,
            "Katana-Mutation-Id": record.mutation_id,
            "Katana-Idempotency-Key-SHA256": record.key_hash,
            "Katana-Request-SHA256": record.request_hash,
            "Katana-Result-SHA256": canonical_request_hash(record.result),
            "Katana-Postimages-SHA256": canonical_request_hash(
                record.postimages
            ),
        }
        if trailers != expected:
            raise RollbackSafetyError(
                f"mutation receipt mismatch: {record.mutation_id}"
            )
        parents = commit_parents(binding.repo_root, commit_sha)
        expected_parents = [record.base_sha] if record.base_sha else []
        if parents != expected_parents:
            raise RollbackSafetyError(
                f"mutation receipt parent mismatch: {record.mutation_id}"
            )
        if set(commit_changed_paths(binding.repo_root, commit_sha)) != set(
            record.changed_paths
        ):
            raise RollbackSafetyError(
                f"mutation receipt path mismatch: {record.mutation_id}"
            )
        if set(record.postimages) != set(record.changed_paths):
            raise RollbackSafetyError(
                f"mutation receipt postimage set mismatch: {record.mutation_id}"
            )
        committed_postimages = {
            path: self._postimage_hash(
                commit_file_image(binding.repo_root, commit_sha, path)
            )
            for path in record.changed_paths
        }
        if committed_postimages != record.postimages:
            raise RollbackSafetyError(
                f"mutation receipt postimage mismatch: {record.mutation_id}"
            )
        if not commit_is_ancestor(binding.repo_root, commit_sha):
            raise RollbackSafetyError(
                f"mutation receipt is not reachable: {record.mutation_id}"
            )

    def _reconcile_pending(
        self,
        binding: DomainBinding,
        record: MutationRecord,
    ) -> MutationRecord:
        ledger = binding.mutation_ledger
        if ledger is None:
            return record
        commit_sha = find_commit_with_trailer(
            binding.repo_root,
            "Katana-Mutation-Id",
            record.mutation_id,
        )
        if commit_sha is not None:
            try:
                if record.state != "PREPARED":
                    raise RollbackSafetyError(
                        "published receipt has no PREPARED ledger state"
                    )
                self._validate_receipt(binding, record, commit_sha)
                if not is_working_tree_clean(binding.repo_root):
                    raise RollbackSafetyError(
                        "published receipt has a dirty repository scene"
                    )
                response = self._prepared_response(record, commit_sha)
                manifest_id = response["manifest"]["manifest_id"]
                binding.manifest.finalize(
                    manifest_id, response["git"],
                )
                return ledger.finalize(
                    record.mutation_id,
                    commit_sha=commit_sha,
                    response=response,
                    verified_head=head_sha(binding.repo_root),
                )
            except Exception as exc:
                current = ledger.get(record.mutation_id)
                if current.state in {"PENDING", "PREPARED"}:
                    ledger.mark_broken(
                        record.mutation_id,
                        {
                            "detail": str(exc),
                            "commit_sha": commit_sha,
                        },
                    )
                raise self._unresolved_error(
                    ledger.get(record.mutation_id)
                ) from exc

        clean_at_base = (
            head_sha(binding.repo_root) == record.base_sha
            and is_working_tree_clean(binding.repo_root)
        )
        if record.state == "PENDING" and clean_at_base:
            return ledger.mark_aborted(
                record.mutation_id,
                "no receipt and no tracked repository effect",
            )

        ledger.mark_broken(
            record.mutation_id,
            {
                "detail": "pending mutation has no provably safe recovery",
                "head": head_sha(binding.repo_root),
                "base_sha": record.base_sha,
                "worktree_clean": is_working_tree_clean(binding.repo_root),
            },
        )
        raise self._unresolved_error(ledger.get(record.mutation_id))

    def _reconcile_runtime_ledger(self, binding: DomainBinding) -> None:
        ledger = binding.mutation_ledger
        if ledger is None:
            return
        current_head = head_sha(binding.repo_root)
        if not current_head:
            raise RollbackSafetyError(
                "cannot reconcile mutation ledger without a Git HEAD"
            )
        verified_head = ledger.get_meta(_VERIFIED_HEAD_META)
        if verified_head is None and ledger.record_count() == 0:
            receipt_commit = find_commit_with_trailer(
                binding.repo_root,
                "Katana-Protocol",
                _RECEIPT_PROTOCOL,
            )
            if receipt_commit is not None:
                raise MutationBrokenError(
                    "runtime mutation ledger is incomplete for Git history",
                    {
                        "state": "BROKEN",
                        "ledger_state": "MISSING",
                        "commit_sha": receipt_commit,
                        "detail": (
                            "reachable idempotency receipts exist but the "
                            "SQLite ledger has no mutation rows"
                        ),
                    },
                )
        history_verified = verified_head == current_head
        if verified_head and not history_verified:
            history_verified = commit_is_ancestor(
                binding.repo_root,
                verified_head,
                current_head,
            )
        if not history_verified:
            for record in ledger.list_committed():
                if (
                    not record.commit_sha
                    or not commit_is_ancestor(
                        binding.repo_root,
                        record.commit_sha,
                        current_head,
                    )
                ):
                    ledger.mark_orphaned(
                        record.mutation_id,
                        {
                            "detail": "committed mutation is no longer reachable",
                            "commit_sha": record.commit_sha,
                            "head": current_head,
                        },
                    )
                    raise self._unresolved_error(
                        ledger.get(record.mutation_id)
                    )
            ledger.set_meta(_VERIFIED_HEAD_META, current_head)
        for record in ledger.list_unresolved():
            if record.state in {"BROKEN", "ORPHANED"}:
                raise self._unresolved_error(record)
            reconciled = self._reconcile_pending(binding, record)
            if reconciled.state in {"BROKEN", "ORPHANED"}:
                raise self._unresolved_error(reconciled)

    @staticmethod
    def _record_failed_claim(
        binding: DomainBinding,
        record: MutationRecord | None,
        rollback: dict[str, Any],
        exc: Exception | None = None,
    ) -> None:
        ledger = binding.mutation_ledger
        if ledger is None or record is None:
            return
        try:
            current = ledger.get(record.mutation_id)
            evidence = {
                "detail": str(exc) if exc is not None else rollback["detail"],
                "rollback": rollback,
            }
            if (
                rollback.get("state") == "ROLLED_BACK"
                and current.state in {"PENDING", "PREPARED"}
            ):
                ledger.mark_aborted(record.mutation_id, evidence)
            elif current.state in {"PENDING", "PREPARED"}:
                ledger.mark_broken(record.mutation_id, evidence)
        except Exception:
            # The durable PENDING/PREPARED row remains a fail-stop gate even if
            # recording richer failure evidence is itself unavailable.
            pass

    def mutate(
        self,
        domain: str,
        op: str,
        args: dict[str, Any],
        *,
        expected_base_sha: str | None = None,
        write_fn: Callable[..., dict[str, Any]] | None = None,
        commit_msg: str | None = None,
        extra_commit_paths: list[str] | None = None,
        idempotency_key: str | None = None,
        idempotency_payload: Any | None = None,
    ) -> dict[str, Any]:
        binding = self.get_binding(domain)
        with repository_mutation_lock(binding.repo_root):
            return self._mutate_locked(
                domain,
                op,
                args,
                expected_base_sha=expected_base_sha,
                write_fn=write_fn,
                commit_msg=commit_msg,
                extra_commit_paths=extra_commit_paths,
                idempotency_key=idempotency_key,
                idempotency_payload=idempotency_payload,
            )

    def _mutate_locked(
        self,
        domain: str,
        op: str,
        args: dict[str, Any],
        *,
        expected_base_sha: str | None = None,
        write_fn: Callable[..., dict[str, Any]] | None = None,
        commit_msg: str | None = None,
        extra_commit_paths: list[str] | None = None,
        idempotency_key: str | None = None,
        idempotency_payload: Any | None = None,
    ) -> dict[str, Any]:
        binding = self.get_binding(domain)

        if idempotency_payload is not None and idempotency_key is None:
            raise ValueError(
                "idempotency_payload requires idempotency_key"
            )
        if idempotency_key is not None and binding.mutation_ledger is None:
            raise ValueError(
                "idempotency_key requires an opt-in SQLite mutation ledger"
            )

        request_hash = None
        if binding.mutation_ledger is not None:
            self._validate_runtime_configuration(binding)
            self._validate_commit_message(commit_msg)
            binding.policy.verify(op, args)
            self._reconcile_runtime_ledger(binding)
            if idempotency_key is not None:
                request_hash = self._request_hash(
                    domain,
                    op,
                    args if idempotency_payload is None else idempotency_payload,
                )
                existing = binding.mutation_ledger.lookup(
                    domain=domain,
                    idempotency_key=idempotency_key,
                    op=op,
                    request_hash=request_hash,
                )
                if existing is not None:
                    if existing.state == "COMMITTED":
                        if existing.response is None:
                            raise self._unresolved_error(existing)
                        return existing.response
                    if existing.state not in {"ABORTED"}:
                        raise self._unresolved_error(existing)
            cas_guard(binding.repo_root, expected_base_sha)
        else:
            cas_guard(binding.repo_root, expected_base_sha)
            binding.policy.verify(op, args)

        if write_fn is None:
            raise ValueError("write_fn is required for GovernedKernel.mutate")

        # The governed writer owns the whole repository for the duration of one
        # mutation. Refuse any pre-existing tracked, staged, or untracked state
        # before write_fn can run.
        base_sha = require_clean_working_tree(binding.repo_root)
        if expected_base_sha is not None and base_sha != expected_base_sha:
            raise CASRejectionError(
                f"CAS mismatch: expected {expected_base_sha[:8]}..., "
                f"got {base_sha[:8]}..."
            )
        validate_transaction_paths(binding.repo_root, [binding.ledger.path])
        self._validate_runtime_configuration(binding)
        journal = TransactionJournal(binding.repo_root, base_sha)
        binding.vfs.begin_transaction(journal)
        claim_record = None
        try:
            if idempotency_key is not None:
                claim = binding.mutation_ledger.claim(
                    domain=domain,
                    op=op,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    base_sha=base_sha,
                    folder_id=args.get("folder_id"),
                    source_session_id=args.get("source_session_id"),
                )
                claim_record = claim.record
                if not claim.created:
                    raise self._unresolved_error(claim.record)
            try:
                result = write_fn(binding=binding, args=args)
                if not isinstance(result, dict):
                    raise TypeError("write_fn must return a dict")
                changed_paths = list(result.pop("changed_paths", []))
                declared_paths = validate_transaction_paths(
                    binding.repo_root, changed_paths,
                )
                journaled = set(journal.paths)
                if set(declared_paths) != journaled:
                    raise RollbackSafetyError(
                        "write_fn changed_paths do not match explicit VFS journal"
                    )
            except Exception as exc:
                rollback = journal.rollback()
                self._record_failed_claim(
                    binding, claim_record, rollback, exc,
                )
                if rollback["state"] == "BROKEN":
                    raise MutationBrokenError(
                        "governed mutation is BROKEN; repository scene preserved",
                        rollback,
                    ) from exc
                raise

            try:
                if op == "delete" and "id" in result:
                    journal.record_disk_state(binding.ledger.path)
                    binding.ledger.tombstone(result["id"])
                    journal.record_disk_state(binding.ledger.path)
                if "tombstoned_ids" in result:
                    for rid in result.pop("tombstoned_ids", []):
                        journal.record_disk_state(binding.ledger.path)
                        binding.ledger.tombstone(rid)
                        journal.record_disk_state(binding.ledger.path)

                manifest_record = binding.manifest.record(
                    domain,
                    op,
                    result,
                    changed_paths=changed_paths,
                    before_write=(
                        journal.capture_path
                        if binding.manifest.git_tracked
                        else None
                    ),
                )
                staging_path = os.path.join(
                    binding.manifest.staging_dir,
                    f"{manifest_record['manifest_id']}.json",
                )
                manifest_path = os.path.join(
                    binding.manifest.manifests_dir,
                    f"{manifest_record['manifest_id']}.json",
                )
                if binding.manifest.git_tracked:
                    journal.record_disk_state(staging_path)
                    journal.record_rename(staging_path, manifest_path)
                committed_manifest_ids = binding.manifest.commit_manifests(
                    [manifest_record["manifest_id"]],
                )

                all_paths = list(changed_paths)
                for path in extra_commit_paths or []:
                    if path not in all_paths:
                        all_paths.append(path)
                ledger_path = validate_transaction_paths(
                    binding.repo_root, [binding.ledger.path],
                )[0]
                if ledger_path in journal.paths and ledger_path not in all_paths:
                    all_paths.append(ledger_path)
                if binding.manifest.git_tracked:
                    for manifest_name in committed_manifest_ids["manifests"]:
                        committed_manifest_path = os.path.join(
                            binding.manifest.manifests_dir, manifest_name,
                        )
                        if committed_manifest_path not in all_paths:
                            all_paths.append(committed_manifest_path)
                all_paths = validate_transaction_paths(
                    binding.repo_root, all_paths,
                )
                journal.verify_worktree()
                commit_message = commit_msg or f"chore({domain}): {op}"
                commit_images = journal.expected_images(all_paths)
                if claim_record is not None:
                    receipt_paths = journal.effective_paths(all_paths)
                    prepared_result = {
                        "operation_result": result,
                        "manifest_id": manifest_record["manifest_id"],
                    }
                    postimages = {
                        path: self._postimage_hash(commit_images[path])
                        for path in receipt_paths
                    }
                    claim_record = binding.mutation_ledger.prepare(
                        claim_record.mutation_id,
                        result=prepared_result,
                        changed_paths=receipt_paths,
                        postimages=postimages,
                    )
                    commit_message = self._receipt_commit_message(
                        commit_message,
                        claim_record,
                        canonical_request_hash(prepared_result),
                    )
                git_result = git_commit(
                    binding.repo_root,
                    commit_message,
                    all_paths,
                    expected_images=commit_images,
                    expected_base_sha=base_sha,
                )
            except Exception as exc:
                rollback = journal.rollback()
                self._record_failed_claim(
                    binding, claim_record, rollback, exc,
                )
                raise MutationBrokenError(
                    "governed mutation is BROKEN; repository scene preserved",
                    rollback,
                ) from exc

            if not git_result.get("committed"):
                rollback = journal.rollback()
                self._record_failed_claim(
                    binding, claim_record, rollback,
                )
                if rollback["state"] == "BROKEN":
                    raise MutationBrokenError(
                        "governed commit failed; manual recovery required",
                        rollback,
                    )
                response = {
                    **{k: v for k, v in result.items()},
                    "git": git_result,
                    "manifest": {"manifest_id": manifest_record["manifest_id"]},
                    "rollback": rollback,
                }
                if claim_record is not None:
                    response["mutation_id"] = claim_record.mutation_id
                return response

            if not binding.manifest.git_tracked:
                try:
                    journal.verify_worktree()
                    binding.manifest.finalize(
                        manifest_record["manifest_id"], git_result,
                    )
                    journal.verify_worktree()
                    response = {
                        **{k: v for k, v in result.items()},
                        "git": git_result,
                        "manifest": {
                            "manifest_id": manifest_record["manifest_id"],
                        },
                    }
                    if claim_record is not None:
                        response["mutation_id"] = claim_record.mutation_id
                        finalized = binding.mutation_ledger.finalize(
                            claim_record.mutation_id,
                            commit_sha=git_result["detail"],
                            response=response,
                            verified_head=git_result["detail"],
                        )
                        if finalized.response is None:
                            raise RollbackSafetyError(
                                "committed mutation response was not persisted"
                            )
                        response = finalized.response
                except Exception as exc:
                    raise MutationBrokenError(
                        "governed runtime state finalization failed; "
                        "repository scene preserved",
                        journal.rollback(),
                    ) from exc
                return response

            try:
                journal.verify_worktree()
                manifest_image = next(
                    iter(journal.expected_images([manifest_path]).values())
                )
                manifest_data = json.loads(manifest_image.data)
                manifest_data["git"] = git_result
                manifest_bytes = json.dumps(manifest_data, indent=2).encode("utf-8")
                journal.record_write(manifest_path, manifest_bytes)
                with open(manifest_path, "wb") as manifest_file:
                    manifest_file.write(manifest_bytes)
                journal.confirm_write(manifest_path, manifest_bytes)

                committed_sha = git_result.get("detail", "")
                amend_result = amend_commit(
                    binding.repo_root,
                    [manifest_path],
                    expected_images=journal.expected_images([manifest_path]),
                    expected_base_sha=committed_sha,
                )
                journal.verify_worktree()
            except Exception as exc:
                raise MutationBrokenError(
                    "governed mutation is BROKEN; repository scene preserved",
                    journal.rollback(),
                ) from exc

            if not amend_result.get("committed"):
                rollback = journal.rollback()
                if rollback["state"] == "BROKEN":
                    raise MutationBrokenError(
                        "governed manifest amend failed; manual recovery required",
                        rollback,
                    )
                return {
                    **{k: v for k, v in result.items()},
                    "git": amend_result,
                    "manifest": {"manifest_id": manifest_record["manifest_id"]},
                    "rollback": rollback,
                }

            return {
                **{k: v for k, v in result.items()},
                "git": amend_result,
                "manifest": {"manifest_id": manifest_record["manifest_id"]},
            }
        finally:
            binding.vfs.end_transaction(journal)
