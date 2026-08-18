"""GovernedKernel: single authoritative-writer binding + unique mutate entry point.

Orchestrates the full governance chain:
  authorize -> CAS(expected_base_sha) -> policy -> VFS -> ledger -> manifest -> git commit
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from katana_kernel.gitops import (
    BaseCommitConflictError,
    CASRejectionError,
    DirtyWorkTreeError,
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
    governed_dirty_paths,
    head_sha,
    is_path_ignored,
    is_working_tree_clean,
    orphan_index_lock_path,
    read_katana_commit_trailers,
    read_worktree_image,
    repository_mutation_lock,
    require_exact_git_root,
    require_clean_working_tree,
    staged_paths,
    tracked_modified_paths,
    unstage_paths,
    untracked_not_ignored_paths,
    validate_runtime_state_paths,
    validate_runtime_state_tree,
    validate_transaction_paths,
    worktree_matches_head,
)
from katana_kernel.idempotency import (
    MutationRecord,
    canonical_request_hash,
)


_RECEIPT_PROTOCOL = "katana-idempotency-v1"
_RESERVED_TRAILER_PREFIX = "Katana-"
_VERIFIED_HEAD_META = "verified_head"

# EK-4 artifact-class judgment for reconcile recovery type 1: only generated
# side-effect files are safe to auto-quarantine.  Primary content stays for the
# governed commit path or an operator decision (type 6 diagnosis).
_ARTIFACT_DIR_SEGMENTS = frozenset({
    "artifacts", "products", "product", "build", "dist", "out",
    "generated", "cache", "node_modules", "target", "__pycache__", ".cache",
})
_ARTIFACT_SUFFIXES = frozenset({
    ".log", ".tmp", ".temp", ".part", ".swp", ".swo", ".bak",
    ".download", ".crdownload", ".lock", ".pid",
})


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
        resolved = require_exact_git_root(repo_root)
        if os.path.realpath(vfs.root) != resolved:
            raise ValueError(
                "GovernedVFS root must equal the exact bound Git repo_root"
            )
        if resolved in self._repo_roots:
            raise ValueError(f"repo_root {resolved!r} already bound by another domain")
        binding = DomainBinding(
            domain=domain,
            policy=policy,
            vfs=vfs,
            ledger=ledger,
            manifest=manifest,
            repo_root=resolved,
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
    def _recovered_root(binding: DomainBinding) -> str:
        """Return the quarantine/recovered runtime root for this binding."""
        return os.path.join(
            os.path.dirname(binding.manifest.manifests_dir), "recovered",
        )

    @classmethod
    def _runtime_state_allowances(cls, binding: DomainBinding) -> list[str]:
        """Return the exact ignored runtime paths owned by this binding."""
        recovered_root = cls._recovered_root(binding)
        if binding.manifest.git_tracked:
            # git_tracked manifests live under a tracked .katana directory, so
            # there is no runtime manifests allowance; the recovered root is
            # still the ignore-allowance used by reconcile (valid only when the
            # repo actually ignores it).
            return [recovered_root]
        allowances = [
            binding.manifest.manifests_dir,
            recovered_root,
        ]
        if binding.mutation_ledger is not None:
            ledger_path = binding.mutation_ledger.path
            allowances.extend(
                [
                    ledger_path,
                    f"{ledger_path}-wal",
                    f"{ledger_path}-shm",
                    f"{ledger_path}-journal",
                ]
            )
        return allowances

    @staticmethod
    def _control_paths(
        binding: DomainBinding,
        extra: list[str] | None = None,
    ) -> list[str]:
        """Return the governance surfaces a mutation must always touch.

        These paths are outside the folder scope but still block a governed
        mutation when dirty (the governance surface is never exempt from the
        clean guard).
        """
        paths: list[str] = [binding.ledger.path]
        if binding.manifest.git_tracked:
            paths.append(binding.manifest.manifests_dir)
        if binding.mutation_ledger is not None:
            paths.append(binding.mutation_ledger.path)
        if extra:
            paths.extend(extra)
        return paths

    def reconcile(
        self,
        domain: str,
        *,
        scope_prefixes: list[str] | None = None,
        control_paths: list[str] | None = None,
        recover: bool = False,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate and reconcile one runtime binding before serving traffic.

        With ``recover=False`` this is the legacy verify-only path: ``head`` and
        ``unresolved`` keys are preserved and a dirty scene fails closed.  With
        ``recover=True`` the safe recovery checklist (types 1-5) is classified
        *before* any mutation is applied; only when no unattributable residue
        remains are the recoverable actions executed, and any remainder fails
        closed with a structured ``BROKEN`` diagnosis and the tree left
        untouched (type 6).

        With an ``idempotency_key`` the completed recovery is recorded as a
        governed mutation claim so an identical retry replays the recorded
        response instead of re-executing quarantine moves, resets, or commits.
        """
        binding = self.get_binding(domain)
        if idempotency_key is not None and binding.mutation_ledger is None:
            raise ValueError(
                "idempotency_key requires an opt-in SQLite mutation ledger"
            )
        with repository_mutation_lock(binding.repo_root):
            self._validate_runtime_configuration(binding)
            payload = {
                "scope_prefixes": list(scope_prefixes or []),
                "control_paths": list(control_paths or []),
            }
            if idempotency_key is not None:
                replay = self._reconcile_replay(
                    binding, idempotency_key, payload,
                )
                if replay is not None:
                    return replay
            recovered: list[dict[str, Any]] = []
            if recover:
                recovered = self._recover_governed_state(
                    binding, scope_prefixes, control_paths,
                )
            try:
                require_clean_working_tree(
                    binding.repo_root,
                    allowed_ignored_paths=self._runtime_state_allowances(binding),
                    scope_prefixes=scope_prefixes,
                    control_paths=self._control_paths(binding, control_paths),
                )
            except DirtyWorkTreeError as exc:
                if not recover:
                    raise
                raise self._unrecoverable_scene_error(binding) from exc
            if binding.mutation_ledger is not None:
                before_unresolved = {
                    record.mutation_id: record.state
                    for record in binding.mutation_ledger.list_unresolved()
                }
                self._reconcile_runtime_ledger(binding)
                if recover:
                    for mutation_id, state in before_unresolved.items():
                        resolved = (
                            binding.mutation_ledger.get(mutation_id).state
                            not in {"PENDING", "PREPARED", "BROKEN", "ORPHANED"}
                        )
                        if resolved:
                            recovered.append({
                                "type": "ledger_reconciled",
                                "mutation_id": mutation_id,
                                "from": state,
                            })
            result: dict[str, Any] = {
                "ok": True,
                "domain": domain,
                "head": head_sha(binding.repo_root),
                "unresolved": (
                    len(binding.mutation_ledger.list_unresolved())
                    if binding.mutation_ledger is not None
                    else 0
                ),
            }
            if recover:
                result["recovered"] = recovered
            if idempotency_key is not None and recover:
                result = self._record_reconcile_result(
                    binding, idempotency_key, payload, result,
                )
            return result

    def _reconcile_replay(
        self,
        binding: DomainBinding,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return a previously committed reconcile response, if any."""
        ledger = binding.mutation_ledger
        if ledger is None:
            return None
        request_hash = self._request_hash(
            binding.domain, "wf_reconcile", payload,
        )
        existing = ledger.lookup(
            domain=binding.domain,
            idempotency_key=idempotency_key,
            op="wf_reconcile",
            request_hash=request_hash,
        )
        if existing is None:
            return None
        if existing.state == "COMMITTED":
            if existing.response is None:
                raise self._unresolved_error(existing)
            return copy.deepcopy(existing.response)
        if existing.state != "ABORTED":
            raise self._unresolved_error(existing)
        return None

    def _record_reconcile_result(
        self,
        binding: DomainBinding,
        idempotency_key: str,
        payload: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a completed reconcile as an idempotent governed mutation."""
        ledger = binding.mutation_ledger
        if ledger is None:
            return result
        request_hash = self._request_hash(
            binding.domain, "wf_reconcile", payload,
        )
        base_sha = head_sha(binding.repo_root)
        claim = ledger.claim(
            domain=binding.domain,
            op="wf_reconcile",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            base_sha=base_sha,
        )
        record = claim.record
        if not claim.created:
            if record.state == "COMMITTED":
                return copy.deepcopy(record.response or result)
            raise self._unresolved_error(record)
        changed_paths = sorted({
            item.get("path")
            for item in result.get("recovered", [])
            if item.get("path")
        })
        ledger.prepare(
            record.mutation_id,
            result={"operation_result": result, "manifest_id": "wf_reconcile"},
            changed_paths=changed_paths,
            postimages={},
        )
        finalized = ledger.finalize(
            record.mutation_id,
            commit_sha=head_sha(binding.repo_root),
            response=result,
            verified_head=head_sha(binding.repo_root),
        )
        return finalized.response if finalized.response is not None else result

    @staticmethod
    def _under_scope(path: str, scope_prefixes: list[str] | None) -> bool:
        """True only for an explicit, non-empty scope that contains ``path``."""
        if not scope_prefixes:
            return False
        for prefix in scope_prefixes:
            prefix = prefix.rstrip("/")
            if path == prefix or path.startswith(f"{prefix}/"):
                return True
        return False

    @staticmethod
    def _is_artifact_class(path: str) -> bool:
        """EK-4 artifact-class judgment for one repo-relative untracked path.

        Only generated side-effect files are safe to auto-quarantine.  Primary
        content stays for the governed commit path or an operator decision and
        is therefore left untouched for the type-6 diagnosis.
        """
        parts = path.split("/")
        if any(part in _ARTIFACT_DIR_SEGMENTS for part in parts[:-1]):
            return True
        name = parts[-1]
        return any(name.endswith(suffix) for suffix in _ARTIFACT_SUFFIXES)

    def _recovered_root_is_ignored(self, binding: DomainBinding) -> bool:
        probe = os.path.join(self._recovered_root(binding), ".path-probe")
        return is_path_ignored(binding.repo_root, probe)

    def _quarantine_untracked(self, binding: DomainBinding, path: str) -> str | None:
        """Move one untracked artifact into the ignored recovered root.

        Returns the repo-relative destination on success, or ``None`` when the
        recovered root is not an ignored runtime path (the caller then leaves
        the file in place for the type-6 diagnosis instead of displacing it).
        """
        repo = Path(binding.repo_root)
        destination_root = Path(self._recovered_root(binding))
        if not self._recovered_root_is_ignored(binding):
            return None

        source = repo / path
        digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:12]
        destination_dir = destination_root / digest
        destination_dir.mkdir(parents=True, exist_ok=True)

        base_name = source.name
        destination = destination_dir / base_name
        suffix = 1
        while destination.exists():
            stem = Path(base_name).stem
            ext = Path(base_name).suffix
            destination = destination_dir / f"{stem}.{suffix}{ext}"
            suffix += 1

        moved_to = str(destination.relative_to(repo))
        shutil.move(str(source), str(destination))

        manifest_path = destination_root / "quarantine-manifest.json"
        records = self._load_quarantine_manifest(manifest_path)
        records.append({
            "source": path,
            "moved_to": moved_to,
            "reason": "untracked-not-ignored-artifact-under-scope",
        })
        try:
            self._write_quarantine_manifest(manifest_path, records)
        except OSError as exc:
            shutil.move(str(destination), str(source))
            raise RollbackSafetyError(
                f"cannot persist quarantine pointer for {path!r}"
            ) from exc
        return moved_to

    @staticmethod
    def _load_quarantine_manifest(manifest_path: Path) -> list:
        if not manifest_path.exists():
            return []
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _write_quarantine_manifest(manifest_path: Path, records: list) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = manifest_path.with_name(f"{manifest_path.name}.tmp")
        with temporary.open("wb") as output:
            output.write(json.dumps(records, indent=2).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(manifest_path))

    def _unrecoverable_scene_error(
        self,
        binding: DomainBinding,
        *,
        mutation_id: str | None = None,
        paths: list[str] | None = None,
    ) -> MutationBrokenError:
        dirty = list(paths) if paths is not None else self._enumerate_dirty_paths(binding)
        if mutation_id is None:
            mutation_id = self._attribute_mutation_id(binding, dirty)
        return MutationBrokenError(
            "governed reconciliation stopped: incompatible scene requires "
            "manual recovery",
            {
                "state": "BROKEN",
                "mutation_id": mutation_id,
                "paths": dirty,
                "suggested_commands": [
                    "git status --porcelain=v1 --no-renames",
                    f"git diff -- {(' '.join(dirty)) if dirty else '.'}",
                    "git log --oneline -5",
                ],
                "detail": (
                    "residual tracked/untracked state could not be attributed "
                    "to a safely recoverable transaction"
                ),
            },
        )

    def _attribute_mutation_id(
        self,
        binding: DomainBinding,
        dirty: list[str],
    ) -> str | None:
        if binding.mutation_ledger is None:
            return None
        dirty_set = set(dirty)
        fallback: str | None = None
        for record in binding.mutation_ledger.list_unresolved():
            if not (set(record.changed_paths) & dirty_set):
                continue
            if record.state == "BROKEN":
                return record.mutation_id
            if fallback is None:
                fallback = record.mutation_id
        return fallback

    def _enumerate_dirty_paths(self, binding: DomainBinding) -> list[str]:
        repo = binding.repo_root
        paths: list[str] = []
        for path in untracked_not_ignored_paths(repo):
            if path not in paths:
                paths.append(path)
        for path in tracked_modified_paths(repo):
            if path not in paths:
                paths.append(path)
        return paths

    @staticmethod
    def _prepared_records(binding: DomainBinding) -> list[Any]:
        ledger = binding.mutation_ledger
        if ledger is None:
            return []
        return ledger.list_by_states({"PREPARED"})

    def _prepared_commit_resumable(
        self,
        binding: DomainBinding,
        record: Any,
    ) -> bool:
        repo = binding.repo_root
        if find_commit_with_trailer(
            repo, "Katana-Mutation-Id", record.mutation_id,
        ) is not None:
            return False
        changed_paths = list(record.changed_paths)
        if not changed_paths:
            return False
        try:
            for path in changed_paths:
                image = read_worktree_image(repo, path)
                if self._postimage_hash(image) != record.postimages.get(path):
                    return False
            return True
        except (RollbackSafetyError, KeyError, OSError):
            return False

    def _resume_prepared_commit(
        self,
        binding: DomainBinding,
        record: Any,
    ) -> bool:
        if binding.mutation_ledger is None:
            return False
        if not self._prepared_commit_resumable(binding, record):
            return False
        try:
            message = self._receipt_commit_message(
                f"chore({record.domain}): {record.op}",
                record,
                canonical_request_hash(record.result),
            )
            git_result = git_commit(
                binding.repo_root,
                message,
                list(record.changed_paths),
                expected_base_sha=record.base_sha,
            )
            return bool(git_result.get("committed"))
        except (RollbackSafetyError, KeyError, OSError):
            return False

    def _recover_governed_state(
        self,
        binding: DomainBinding,
        scope_prefixes: list[str] | None,
        control_paths: list[str] | None,
    ) -> list[dict[str, Any]]:
        repo = binding.repo_root
        allowances = self._runtime_state_allowances(binding)
        controls = self._control_paths(binding, control_paths)

        # Phase 1: read the blocking scene and classify *before* mutating.
        changed, unexpected_ignored = governed_dirty_paths(
            repo,
            allowed_ignored_paths=allowances,
            scope_prefixes=scope_prefixes,
            control_paths=controls,
        )
        changed_set = set(changed)
        tracked = set(tracked_modified_paths(repo))
        staged = set(staged_paths(repo))
        untracked = set(untracked_not_ignored_paths(repo))

        blocked_tracked = sorted(changed_set & tracked)
        blocked_staged_only = sorted((changed_set & staged) - tracked)
        blocked_untracked = sorted(changed_set & untracked)

        # Type 2: tracked dirt exactly matching a PREPARED postimage.
        resumable_records = [
            record for record in self._prepared_records(binding)
            if self._prepared_commit_resumable(binding, record)
        ]
        resumable_paths = {
            path for record in resumable_records for path in record.changed_paths
        }
        unrecoverable_tracked = sorted(
            path for path in blocked_tracked if path not in resumable_paths
        )

        # Type 3: index-only staged entry whose worktree matches HEAD.
        recoverable_staged: list[str] = []
        unrecoverable_staged: list[str] = []
        for path in blocked_staged_only:
            if worktree_matches_head(repo, path):
                recoverable_staged.append(path)
            else:
                unrecoverable_staged.append(path)

        # Type 1: untracked artifact under an explicit scope with an ignored
        # recovered root.  Non-artifact files and control-surface files are not
        # auto-moved; they stay for the operator (type 6).
        recoverable_untracked: list[str] = []
        unrecoverable_untracked: list[str] = []
        if (scope_prefixes and self._recovered_root_is_ignored(binding)):
            for path in blocked_untracked:
                if (
                    self._under_scope(path, scope_prefixes)
                    and self._is_artifact_class(path)
                ):
                    recoverable_untracked.append(path)
                else:
                    unrecoverable_untracked.append(path)
        else:
            unrecoverable_untracked = blocked_untracked

        orphan_lock = orphan_index_lock_path(repo)
        residue = sorted(
            unrecoverable_tracked
            + unrecoverable_staged
            + unrecoverable_untracked
            + list(unexpected_ignored)
        )

        # Type 6: refuse to touch a scene that will not reach a clean gate.
        if residue:
            raise self._unrecoverable_scene_error(binding, paths=residue)

        recovered: list[dict[str, Any]] = []

        # Type 5: the orphan .git/index.lock unblocks the primitives below.
        if orphan_lock:
            os.unlink(orphan_lock)
            recovered.append({
                "type": "orphan_index_lock",
                "path": ".git/index.lock",
            })

        # Type 2: resume prepared commits whose worktree matches postimages.
        for record in resumable_records:
            if self._resume_prepared_commit(binding, record):
                recovered.append({
                    "type": "resume_commit",
                    "mutation_id": record.mutation_id,
                })

        # Type 3: unstage index-only entries.
        if recoverable_staged:
            unstage_paths(repo, recoverable_staged)
            for path in recoverable_staged:
                recovered.append({"type": "index_only_staged", "path": path})

        # Type 1: quarantine artifact-class untracked files.
        for path in recoverable_untracked:
            moved_to = self._quarantine_untracked(binding, path)
            recovered.append({
                "type": "untracked_quarantined",
                "path": path,
                "moved_to": moved_to,
            })

        return recovered

    def replay_idempotent(
        self,
        domain: str,
        op: str,
        args: dict[str, Any],
        *,
        idempotency_key: str | None,
        idempotency_payload: Any | None = None,
    ) -> dict[str, Any] | None:
        """Return a reconciled committed response before stateful prechecks."""
        if idempotency_key is None:
            return None
        binding = self.get_binding(domain)
        if binding.mutation_ledger is None:
            raise ValueError(
                "idempotency_key requires an opt-in SQLite mutation ledger"
            )
        with repository_mutation_lock(binding.repo_root):
            self._validate_runtime_configuration(binding)
            binding.policy.verify(op, args)
            self._reconcile_runtime_ledger(binding)
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
            if existing is None or existing.state == "ABORTED":
                return None
            if existing.state != "COMMITTED" or existing.response is None:
                raise self._unresolved_error(existing)
            return copy.deepcopy(existing.response)

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
                if not is_working_tree_clean(
                    binding.repo_root,
                    allowed_ignored_paths=self._runtime_state_allowances(binding),
                ):
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
            and is_working_tree_clean(
                binding.repo_root,
                allowed_ignored_paths=self._runtime_state_allowances(binding),
            )
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
                "worktree_clean": is_working_tree_clean(
                    binding.repo_root,
                    allowed_ignored_paths=self._runtime_state_allowances(binding),
                ),
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

    def _handle_base_commit_conflict(
        self,
        binding: DomainBinding,
        journal: TransactionJournal,
        claim_record: MutationRecord | None,
        git_result: dict[str, Any],
    ) -> None:
        """Restore a lost CAS race to a clean scene and signal a retryable loser.

        The loser's transaction bytes are rolled back to HEAD's committed
        images (rather than a BROKEN fail-stop), so the killed volume is never
        locked by leftover uncommitted writes.  A caller receives a retryable
        ``BaseCommitConflictError`` carrying the winner's head SHA.
        """
        rollback = journal.rollback_to_head()
        self._record_failed_claim(binding, claim_record, rollback)
        if rollback["state"] == "BROKEN":
            raise MutationBrokenError(
                "governed commit lost the CAS race but the scene could not be "
                "restored; manual recovery required",
                rollback,
            )
        raise BaseCommitConflictError(
            git_result.get("detail") or "governed commit lost the CAS race",
            head=git_result.get("head"),
        )

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
        scope_prefixes: list[str] | None = None,
        control_paths: list[str] | None = None,
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
                scope_prefixes=scope_prefixes,
                control_paths=control_paths,
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
        scope_prefixes: list[str] | None = None,
        control_paths: list[str] | None = None,
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

        # The governed writer owns the checked scope for the duration of one
        # mutation.  With no scope_prefixes this refuses any pre-existing
        # tracked, staged, or untracked state before write_fn can run; with a
        # scope it refuses dirt only inside the scope + governance control
        # paths, leaving sibling dirt byte-for-byte untouched.
        base_sha = require_clean_working_tree(
            binding.repo_root,
            allowed_ignored_paths=self._runtime_state_allowances(binding),
            scope_prefixes=scope_prefixes,
            control_paths=self._control_paths(binding, control_paths),
        )
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
                if git_result.get("code") == "BASE_COMMIT_CONFLICT":
                    self._handle_base_commit_conflict(
                        binding,
                        journal,
                        claim_record,
                        git_result,
                    )
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
