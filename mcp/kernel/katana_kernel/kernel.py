"""GovernedKernel: single authoritative-writer binding + unique mutate entry point.

Orchestrates the full governance chain:
  authorize -> CAS(expected_base_sha) -> policy -> VFS -> ledger -> manifest -> git commit
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Callable
from typing import Any

from katana_kernel.gitops import (
    CASRejectionError,
    RollbackSafetyError,
    TransactionJournal,
    amend_commit,
    cas_guard,
    git_commit,
    head_sha,
    repository_mutation_lock,
    require_clean_working_tree,
    validate_transaction_paths,
)


@dataclasses.dataclass
class DomainBinding:
    domain: str
    policy: "DomainPolicy"
    vfs: "GovernedVFS"
    ledger: "ResourceIdLedger"
    manifest: "TransactionManifest"
    repo_root: str


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
    ) -> DomainBinding:
        if domain in self._bindings:
            raise ValueError(f"domain {domain!r} already bound")
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
        )
        self._bindings[domain] = binding
        self._repo_roots.add(resolved)
        return binding

    def get_binding(self, domain: str) -> DomainBinding:
        if domain not in self._bindings:
            raise ValueError(f"domain {domain!r} not bound")
        return self._bindings[domain]

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
    ) -> dict[str, Any]:
        binding = self.get_binding(domain)

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
        validate_transaction_paths(
            binding.repo_root,
            [
                binding.ledger.path,
                os.path.join(binding.manifest.manifests_dir, ".path-probe"),
            ],
        )
        journal = TransactionJournal(binding.repo_root, base_sha)
        binding.vfs.begin_transaction(journal)
        try:
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
                    before_write=journal.capture_path,
                )
                staging_path = os.path.join(
                    binding.manifest.staging_dir,
                    f"{manifest_record['manifest_id']}.json",
                )
                journal.record_disk_state(staging_path)
                manifest_path = os.path.join(
                    binding.manifest.manifests_dir,
                    f"{manifest_record['manifest_id']}.json",
                )
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
                git_result = git_commit(
                    binding.repo_root,
                    commit_msg or f"chore({domain}): {op}",
                    all_paths,
                    expected_images=journal.expected_images(all_paths),
                    expected_base_sha=base_sha,
                )
            except Exception as exc:
                rollback = journal.rollback()
                raise MutationBrokenError(
                    "governed mutation is BROKEN; repository scene preserved",
                    rollback,
                ) from exc

            if not git_result.get("committed"):
                rollback = journal.rollback()
                if rollback["state"] == "BROKEN":
                    raise MutationBrokenError(
                        "governed commit failed; manual recovery required",
                        rollback,
                    )
                return {
                    **{k: v for k, v in result.items()},
                    "git": git_result,
                    "manifest": {"manifest_id": manifest_record["manifest_id"]},
                    "rollback": rollback,
                }

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
