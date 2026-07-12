"""GovernedKernel: single authoritative-writer binding + unique mutate entry point.

Orchestrates the full governance chain:
  authorize -> CAS(expected_base_sha) -> policy -> VFS -> ledger -> manifest -> git commit
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

from katana_kernel.gitops import CASRejectionError, _restore_tree, cas_guard, git_commit


@dataclasses.dataclass
class DomainBinding:
    domain: str
    policy: "DomainPolicy"
    vfs: "GovernedVFS"
    ledger: "ResourceIdLedger"
    manifest: "TransactionManifest"
    repo_root: str


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

        cas_guard(binding.repo_root, expected_base_sha)

        binding.policy.verify(op, args)

        if write_fn is None:
            raise ValueError("write_fn is required for GovernedKernel.mutate")

        result = write_fn(binding=binding, args=args)

        changed_paths = list(result.pop("changed_paths", []))
        tombstoned_id = None
        if op == "delete" and "id" in result:
            tombstoned_id = result["id"]
            binding.ledger.tombstone(result["id"])

        manifest_record = binding.manifest.record(domain, op, result)
        committed_manifest_ids = binding.manifest.commit_manifests()

        manifest_path = os.path.join(
            binding.manifest.manifests_dir,
            f"{manifest_record['manifest_id']}.json",
        )
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
        manifest_data["git"] = {"committed": True}
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        all_paths = list(changed_paths)
        for p in extra_commit_paths or []:
            if p not in all_paths:
                all_paths.append(p)

        ledger_path = binding.ledger.path
        if ledger_path not in all_paths:
            if os.path.exists(ledger_path):
                all_paths.append(ledger_path)

        import glob as _glob
        for mp in sorted(_glob.glob(str(binding.manifest.manifests_dir) + "/*.json")):
            if mp not in all_paths:
                all_paths.append(mp)

        git_result = git_commit(
            binding.repo_root,
            commit_msg or f"chore({domain}): {op}",
            all_paths,
        )

        if not git_result.get("committed"):
            binding.manifest.rollback_committed(committed_manifest_ids["manifests"])
            if tombstoned_id is not None:
                binding.ledger.rollback_tombstone(tombstoned_id)
            _restore_tree(binding.repo_root)
            return {
                **{k: v for k, v in result.items()},
                "git": git_result,
                "manifest": {"manifest_id": manifest_record["manifest_id"]},
            }

        return {
            **{k: v for k, v in result.items()},
            "git": git_result,
            "manifest": {"manifest_id": manifest_record["manifest_id"]},
        }