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

from katana_kernel.gitops import CASRejectionError, _restore_tree, cas_guard, git_commit, head_sha, is_working_tree_clean


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
        if op == "delete" and "id" in result:
            binding.ledger.tombstone(result["id"])

        manifest_record = binding.manifest.record(domain, op, result)
        committed_manifest_ids = binding.manifest.commit_manifests()

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
            expected_base_sha=expected_base_sha,
        )

        if not git_result.get("committed"):
            binding.manifest.rollback_committed(committed_manifest_ids["manifests"])
            _restore_tree(binding.repo_root)
            return {
                **{k: v for k, v in result.items()},
                "git": git_result,
                "manifest": {"manifest_id": manifest_record["manifest_id"]},
            }

        manifest_path = os.path.join(
            binding.manifest.manifests_dir,
            f"{manifest_record['manifest_id']}.json",
        )
        updated_record = {**manifest_record, "git": git_result}
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(updated_record, f, indent=2)

        subprocess.run(
            ["git", "-C", binding.repo_root, "add", manifest_path],
            capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "-C", binding.repo_root, "commit", "--amend", "--no-edit"],
            capture_output=True, text=True, timeout=30,
        )

        return {
            **{k: v for k, v in result.items()},
            "git": git_result,
            "manifest": {"manifest_id": manifest_record["manifest_id"]},
        }