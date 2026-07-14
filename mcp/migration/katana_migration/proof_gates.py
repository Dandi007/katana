"""Migration proof-gate suite: automated verification of M3b rehearsal output.

Consumes M3a manifest + M3b rehearsal destination, produces structured
verification records per design §9.4: {gate, status, checked, failures[],
evidence_digest}.  Deterministic: same input → same record (fixed
timestamps/committer).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import unicodedata
from pathlib import Path

from katana_migration.inventory import (
    ACTION_ARCHIVE,
    ACTION_ID_BACKFILL,
    ACTION_MERGE,
    ACTION_NORMALIZE,
    ACTION_PRESERVE,
    ACTION_REJECT,
    ACTION_REWRITE,
    MAX_BASENAME_LENGTH,
    MAX_PATH_LENGTH,
    sha256_hex,
)
from katana_migration.rehearsal import (
    DISPOSITION_BROKEN_NEW,
    DISPOSITION_BROKEN_OLD_ACK,
    DISPOSITION_REDIRECTED,
    DISPOSITION_RESOLVED,
    RehearsalEngine,
    _is_binary,
    _is_lfs_pointer,
    _is_nfc_normalized,
)

# ── Production path guard ──────────────────────────────────────────────────────

_PRODUCTION_ROOTS = ["/data/memory", "/data/vault/", "/data/wiki", "/data/work-records"]


def _guard_no_production_paths(*paths: str) -> None:
    for p in paths:
        pp = str(Path(p).resolve())
        for prod in _PRODUCTION_ROOTS:
            prod_p = str(Path(prod).resolve())
            if pp.startswith(prod_p) or pp == prod_p:
                raise RuntimeError(
                    f"Proof gate refused production path: {p} matches {prod}"
                )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _read_json_or_none(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _make_evidence_digest(record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _gate_record(gate: str, status: str, checked: list[str], failures: list[dict]) -> dict:
    return {
        "gate": gate,
        "status": status,
        "checked": checked,
        "failures": failures,
        "evidence_digest": "",
    }


# ── Gate 1: Parity (§8.4 总量不变量) ──────────────────────────────────────────

def parity_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = []
    failures: list[dict] = []
    objects = manifest.get("objects", [])
    summary = manifest.get("summary", {})

    checked.extend([
        "tracked_count",
        "preserved_count",
        "transformed_count",
        "archived_count",
        "rejected_count",
        "unclassified_zero",
        "invariant_holds",
        "destination_object_set_matches_manifest",
        "zero_silent_skip",
        "no_extra_objects",
    ])

    tracked = summary.get("tracked", 0)
    preserved = summary.get("preserved", 0)
    transformed = summary.get("transformed", 0)
    archived = summary.get("archived", 0)
    rejected = summary.get("rejected", 0)
    unclassified = summary.get("unclassified", 0)

    if tracked != preserved + transformed + archived + rejected:
        failures.append({
            "check": "tracked_equals_sum",
            "detail": f"tracked={tracked} != preserved+transformed+archived+rejected="
                       f"{preserved + transformed + archived + rejected}",
        })

    if unclassified != 0:
        failures.append({
            "check": "unclassified_zero",
            "detail": f"unclassified={unclassified} != 0",
        })

    if not summary.get("invariant_holds", False):
        failures.append({
            "check": "invariant_holds",
            "detail": "Manifest invariant_holds is False",
        })

    manifest_dest_paths = {
        obj["destination_path"]: obj
        for obj in objects
    }

    domain_groups: dict[str, list[dict]] = {}
    for obj in objects:
        dest = obj.get("destination_repo", "default")
        domain_groups.setdefault(dest, []).append(obj)

    for dest_repo, domain_objects in domain_groups.items():
        dest_path = Path(dest_root) / dest_repo.lstrip("/")
        if not dest_path.exists():
            continue

        materialized = set()
        expected = set()
        for obj in domain_objects:
            expected.add(obj["destination_path"])
            if obj.get("action") != ACTION_REJECT:
                if obj.get("action") == ACTION_ARCHIVE:
                    materialized.add(f"_archive/{obj['destination_path']}")
                else:
                    materialized.add(obj["destination_path"])

        for root, dirs, files in os.walk(str(dest_path)):
            if ".git" in dirs:
                dirs.remove(".git")
            for fname in files:
                rel = str(Path(root).relative_to(dest_path) / fname)
                if rel.startswith(".migration"):
                    continue
                if rel in ("MIGRATION_BASE.json", "redirects.json", "references.json", ".gitkeep"):
                    continue
                if rel.startswith("_archive/"):
                    inner_rel = rel[len("_archive/"):]
                    if inner_rel not in manifest_dest_paths:
                        failures.append({
                            "check": "extra_archived_object",
                            "detail": f"Archived object '{rel}' not in manifest",
                        })
                elif rel not in manifest_dest_paths:
                    if not rel.endswith(".diff_manifest.json"):
                        failures.append({
                            "check": "extra_object",
                            "detail": f"Object '{rel}' in destination but not in manifest",
                        })

        for obj in domain_objects:
            if obj.get("action") == ACTION_REJECT:
                continue
            obj_path = obj["destination_path"]
            expected_path = dest_path / obj_path
            if obj.get("action") == ACTION_ARCHIVE:
                expected_path = dest_path / "_archive" / obj_path
            if not expected_path.exists():
                failures.append({
                    "check": "missing_object",
                    "detail": f"Manifest object '{obj_path}' not materialized (action={obj.get('action')})",
                })

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("parity", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


# ── Gate 2: Hash reconciliation (§8.5) ─────────────────────────────────────────

def hash_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = []
    failures: list[dict] = []
    objects = manifest.get("objects", [])

    source_sets = manifest.get("source_sets", [])

    for obj in objects:
        action = obj.get("action", ACTION_PRESERVE)
        if action == ACTION_REJECT:
            continue

        dest_repo = obj.get("destination_repo", "default")
        dest_path = Path(dest_root) / dest_repo.lstrip("/") / obj["destination_path"]
        if action == ACTION_ARCHIVE:
            dest_path = Path(dest_root) / dest_repo.lstrip("/") / "_archive" / obj["destination_path"]

        if not dest_path.exists():
            failures.append({
                "check": "hash_object_missing",
                "path": obj["destination_path"],
                "detail": "Destination object not found",
            })
            continue

        try:
            dest_content = dest_path.read_bytes()
        except OSError as e:
            failures.append({
                "check": "hash_read_error",
                "path": obj["destination_path"],
                "detail": str(e),
            })
            continue

        dest_sha = _sha256_hex(dest_content)

        if action == ACTION_PRESERVE:
            checked.append("preserve_destination_sha256")
            expected_sha = obj.get("sha256") or obj.get("pre_hash")
            if expected_sha and dest_sha != expected_sha:
                failures.append({
                    "check": "preserve_sha256_mismatch",
                    "path": obj["destination_path"],
                    "detail": f"expected SHA-256={expected_sha}, got={dest_sha}",
                })

        elif action == ACTION_ID_BACKFILL:
            checked.append("id_backfill_body_bytes_preserved")
            source_root = _find_source_root(obj, source_sets)
            if source_root:
                source_file = Path(source_root) / obj["source_path"]
                if source_file.exists():
                    source_content = source_file.read_bytes()
                    from katana_migration.rehearsal import _extract_body_bytes
                    source_body = _extract_body_bytes(source_content)
                    dest_body = _extract_body_bytes(dest_content)
                    if dest_body != source_body:
                        failures.append({
                            "check": "id_backfill_body_altered",
                            "path": obj["destination_path"],
                            "detail": "Body bytes changed after id_backfill",
                        })

        elif action in (ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE):
            checked.append("transform_pre_hash_source_verify")
            source_root = _find_source_root(obj, source_sets)
            if source_root:
                source_file = Path(source_root) / obj["source_path"]
                if source_file.exists():
                    source_content = source_file.read_bytes()
                    source_sha = _sha256_hex(source_content)
                    pre_hash = obj.get("pre_hash")
                    if pre_hash and source_sha != pre_hash:
                        failures.append({
                            "check": "transform_pre_hash_mismatch",
                            "path": obj["destination_path"],
                            "detail": f"pre_hash={pre_hash} != source SHA-256={source_sha}",
                        })

            checked.append("transform_post_hash_verify")
            pre_hash = obj.get("pre_hash")
            post_hash = obj.get("post_hash")
            if post_hash and pre_hash and post_hash != pre_hash and dest_sha != post_hash:
                failures.append({
                    "check": "transform_post_hash_mismatch",
                    "path": obj["destination_path"],
                    "detail": f"post_hash={post_hash} != destination SHA-256={dest_sha}",
                })

            checked.append("transform_diff_manifest_exists")
            diff_path = Path(str(dest_path) + ".diff_manifest.json")
            if not diff_path.exists():
                failures.append({
                    "check": "transform_diff_manifest_missing",
                    "path": obj["destination_path"],
                    "detail": "No diff_manifest.json found for transform action",
                })

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("hash", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


def _find_source_root(obj: dict, source_sets: list[dict]) -> str | None:
    source_repo = obj.get("source_repo", "")
    source_path = obj.get("source_path", "")
    candidates = []
    for i, ss in enumerate(source_sets):
        if ss.get("source_repo") == source_repo:
            candidates.append((i, ss))
    if len(candidates) == 1:
        return candidates[0][1].get("root")
    for i, ss in candidates:
        root = ss.get("root", "")
        if root and (Path(root) / source_path).exists():
            return root
    for i, ss in candidates:
        root = ss.get("root")
        if root:
            return root
    return None


# ── Gate 3: ID reconciliation (§8.4) ───────────────────────────────────────────

def id_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = [
        "canonical_id_preserved",
        "legacy_id_stable",
        "rejected_id_not_reused",
        "rename_redirect_map_complete",
    ]
    failures: list[dict] = []
    objects = manifest.get("objects", [])
    redirect_map = manifest.get("redirect_map", {})

    assigned_ids: dict[str, set[tuple[str, str, str]]] = {}
    rejected_ids: dict[str, set[tuple[str, str, str]]] = {}

    def identity(obj: dict) -> tuple[str, str, str]:
        if obj.get("object_class") == "work_folder" and obj.get("work_folder_path") is not None:
            return ("work_folder", obj.get("destination_repo", "default"), obj["work_folder_path"])
        return ("object", obj.get("destination_repo", "default"), obj["destination_path"])

    for obj in objects:
        rid = obj.get("domain_resource_id")
        if rid:
            if obj.get("action") == ACTION_REJECT:
                rejected_ids.setdefault(rid, set()).add(identity(obj))
            else:
                assigned_ids.setdefault(rid, set()).add(identity(obj))

    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue
        rid = obj.get("domain_resource_id")
        conflicting_rejections = rejected_ids.get(rid, set()) - {identity(obj)} if rid else set()
        if conflicting_rejections:
            failures.append({
                "check": "rejected_id_reused",
                "path": obj["destination_path"],
                "detail": f"ID {rid} was assigned to a rejected object and is now reused",
            })

    for obj in objects:
        object_class = obj.get("object_class", "")
        if object_class == "memory_canonical":
            dest_repo = obj.get("destination_repo", "default")
            dest_path = Path(dest_root) / dest_repo.lstrip("/") / obj["destination_path"]
            if dest_path.exists():
                try:
                    content = dest_path.read_bytes()
                except OSError:
                    continue
                from katana_migration.rehearsal import _parse_frontmatter
                fm, _, _ = _parse_frontmatter(content)
                if fm and fm.get("id"):
                    dest_id = fm["id"]
                    source_rid = obj.get("domain_resource_id")
                    if source_rid and dest_id != source_rid:
                        failures.append({
                            "check": "canonical_id_changed",
                            "path": obj["destination_path"],
                            "detail": f"Canonical ID changed from {source_rid} to {dest_id}",
                        })

    for obj in objects:
        if obj.get("action") == ACTION_ID_BACKFILL and obj.get("domain_resource_id"):
            dest_repo = obj.get("destination_repo", "default")
            dest_path = Path(dest_root) / dest_repo.lstrip("/") / obj["destination_path"]
            if dest_path.exists():
                try:
                    content = dest_path.read_bytes()
                except OSError:
                    continue
                from katana_migration.rehearsal import _parse_frontmatter
                fm, _, _ = _parse_frontmatter(content)
                if fm and fm.get("id"):
                    dest_id = fm["id"]
                    expected_id = obj["domain_resource_id"]
                    if dest_id != expected_id:
                        failures.append({
                            "check": "backfilled_id_mismatch",
                            "path": obj["destination_path"],
                            "detail": f"Expected backfilled ID {expected_id}, got {dest_id}",
                        })

    if redirect_map:
        manifest_redirects = {
            obj.get("source_path"): obj.get("domain_resource_id")
            for obj in objects
            if obj.get("action") == ACTION_ID_BACKFILL and obj.get("domain_resource_id")
        }
        for src_path, new_id in redirect_map.items():
            if src_path not in manifest_redirects:
                failures.append({
                    "check": "redirect_map_incomplete",
                    "detail": f"Redirect map entry for '{src_path}' not in manifest objects",
                })

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("id", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


# ── Gate 4: Reference reconciliation (§8.5) ────────────────────────────────────

def reference_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = [
        "reference_manifest_present",
        "new_broken_minus_acknowledged_old_broken_equals_zero",
        "resolved_refs_same_target_id",
        "redirected_refs_different_target_id",
        "broken_old_ack_no_old_target",
        "broken_new_no_new_target_but_has_old_target",
    ]
    failures: list[dict] = []

    domain_groups: dict[str, list[dict]] = {}
    for obj in manifest.get("objects", []):
        dest = obj.get("destination_repo", "default")
        domain_groups.setdefault(dest, []).append(obj)

    for dest_repo in domain_groups:
        refs_path = Path(dest_root) / dest_repo.lstrip("/") / "references.json"
        if not refs_path.exists():
            continue

        refs = _read_json_or_none(refs_path)
        if refs is None:
            failures.append({
                "check": "references_json_invalid",
                "detail": f"references.json is missing or invalid in {dest_repo}",
            })
            continue

        entries = refs.get("entries", [])
        old_broken_ack = refs.get("old_broken_acknowledged", 0)
        new_broken = refs.get("new_broken", 0)

        if new_broken - old_broken_ack != 0:
            failures.append({
                "check": "new_broken_minus_acknowledged_old_broken_equals_zero",
                "detail": f"new_broken={new_broken} - old_broken_acknowledged={old_broken_ack} = "
                           f"{new_broken - old_broken_ack} != 0",
            })

        for entry in entries:
            disposition = entry.get("disposition", "")
            old_target_id = entry.get("old_target_id")
            new_target_id = entry.get("new_target_id")

            if disposition == DISPOSITION_RESOLVED:
                if old_target_id != new_target_id:
                    failures.append({
                        "check": "resolved_ref_target_id_mismatch",
                        "detail": f"RESOLVED ref {entry.get('old_literal')}: "
                                  f"old_target_id={old_target_id} != new_target_id={new_target_id}",
                    })
            elif disposition == DISPOSITION_REDIRECTED:
                if old_target_id == new_target_id:
                    failures.append({
                        "check": "redirected_ref_same_target_id",
                        "detail": f"REDIRECTED ref {entry.get('old_literal')}: "
                                  f"old_target_id == new_target_id == {old_target_id}",
                    })
            elif disposition == DISPOSITION_BROKEN_OLD_ACK:
                if old_target_id is not None:
                    failures.append({
                        "check": "broken_old_ack_has_old_target",
                        "detail": f"BROKEN_OLD_ACK ref {entry.get('old_literal')} has "
                                  f"old_target_id={old_target_id} (expected None)",
                    })
            elif disposition == DISPOSITION_BROKEN_NEW:
                if old_target_id is None:
                    failures.append({
                        "check": "broken_new_missing_old_target",
                        "detail": f"BROKEN_NEW ref {entry.get('old_literal')} has "
                                  f"old_target_id=None (expected non-None)",
                    })
                if new_target_id is not None:
                    failures.append({
                        "check": "broken_new_has_new_target",
                        "detail": f"BROKEN_NEW ref {entry.get('old_literal')} has "
                                  f"new_target_id={new_target_id} (expected None)",
                    })

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("reference", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


# ── Gate 5: Integrity (§8.5) ───────────────────────────────────────────────────

def integrity_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = [
        "git_fsck_clean",
        "lfs_pointer_complete",
        "executable_bit",
        "binary_bytes",
        "unicode_nfc",
        "casefold_collision",
        "path_length",
        "symlink",
    ]
    failures: list[dict] = []
    objects = manifest.get("objects", [])

    domain_groups: dict[str, list[dict]] = {}
    for obj in objects:
        dest = obj.get("destination_repo", "default")
        domain_groups.setdefault(dest, []).append(obj)

    for dest_repo, domain_objects in domain_groups.items():
        dest_path = Path(dest_root) / dest_repo.lstrip("/")

        if (dest_path / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "fsck", "--no-dangling", "--strict"],
                    cwd=str(dest_path), capture_output=True, text=True, timeout=30
                )
                if result.returncode != 0:
                    failures.append({
                        "check": "git_fsck",
                        "repo": dest_repo,
                        "detail": f"git fsck failed: {result.stderr.strip()[:500]}",
                    })
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                failures.append({
                    "check": "git_fsck",
                    "repo": dest_repo,
                    "detail": f"git fsck error: {e}",
                })

        basename_casefold: dict[str, str] = {}
        for obj in domain_objects:
            if obj.get("action") == ACTION_REJECT:
                continue

            archive_prefix = ""
            if obj.get("action") == ACTION_ARCHIVE:
                archive_prefix = "_archive/"

            obj_path = Path(obj["destination_path"])
            target = dest_path / archive_prefix / obj_path

            if not target.exists():
                continue

            if target.is_symlink():
                failures.append({
                    "check": "symlink",
                    "path": obj["destination_path"],
                    "detail": "Symlinks are rejected by default",
                })
                continue

            try:
                content = target.read_bytes()
            except OSError as e:
                failures.append({
                    "check": "read_error",
                    "path": obj["destination_path"],
                    "detail": str(e),
                })
                continue

            if _is_binary(content):
                failures.append({
                    "check": "binary_bytes",
                    "path": obj["destination_path"],
                    "detail": "File contains binary bytes",
                })

            if _is_lfs_pointer(content):
                failures.append({
                    "check": "lfs_pointer",
                    "path": obj["destination_path"],
                    "detail": "File is a git LFS pointer",
                })

            if not _is_nfc_normalized(content):
                failures.append({
                    "check": "unicode_nfc",
                    "path": obj["destination_path"],
                    "detail": "Content is not NFC normalized",
                })

            st = target.stat()
            if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                failures.append({
                    "check": "executable_bit",
                    "path": obj["destination_path"],
                    "detail": "File has executable bit set",
                })

            basename = obj_path.name
            basename_encoded = basename.encode("utf-8")
            if len(basename_encoded) > MAX_BASENAME_LENGTH:
                failures.append({
                    "check": "path_length",
                    "path": obj["destination_path"],
                    "detail": f"Basename exceeds {MAX_BASENAME_LENGTH} bytes: {len(basename_encoded)}",
                })

            path_encoded = obj["destination_path"].encode("utf-8")
            if len(path_encoded) > MAX_PATH_LENGTH:
                failures.append({
                    "check": "path_length",
                    "path": obj["destination_path"],
                    "detail": f"Path exceeds 4096 bytes: {len(path_encoded)}",
                })

            casefold_name = basename.casefold()
            if casefold_name in basename_casefold and basename_casefold[casefold_name] != basename:
                failures.append({
                    "check": "casefold_collision",
                    "path": obj["destination_path"],
                    "detail": f"Casefold collision with {basename_casefold[casefold_name]}: {basename}",
                })
            else:
                basename_casefold[casefold_name] = basename

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("integrity", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


# ── Gate 6: History extraction correctness (§8.5) ──────────────────────────────

def history_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = [
        "path_filtered_history_only_in_scope",
        "out_of_scope_leak_detected",
        "filtered_head_content_matches_source",
    ]
    failures: list[dict] = []
    objects = manifest.get("objects", [])

    in_scope_paths = {obj["destination_path"] for obj in objects}

    domain_groups: dict[str, list[dict]] = {}
    for obj in objects:
        dest = obj.get("destination_repo", "default")
        domain_groups.setdefault(dest, []).append(obj)

    for dest_repo, domain_objects in domain_groups.items():
        dest_path = Path(dest_root) / dest_repo.lstrip("/")
        if not (dest_path / ".git").exists():
            continue

        try:
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=str(dest_path), capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                tree_paths = set(result.stdout.strip().split("\n")) - {""}
                scoped = {
                    p for p in tree_paths
                    if not p.startswith(".migration")
                    and p not in ("MIGRATION_BASE.json", "redirects.json", "references.json", ".gitkeep")
                    and not p.startswith("_archive/")
                }
                out_of_scope = scoped - in_scope_paths
                if out_of_scope:
                    failures.append({
                        "check": "out_of_scope_leak",
                        "repo": dest_repo,
                        "detail": f"Out-of-scope paths in HEAD: {sorted(out_of_scope)}",
                    })
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            failures.append({
                "check": "git_ls_tree_error",
                "repo": dest_repo,
                "detail": str(e),
            })

        source_sets = manifest.get("source_sets", [])
        for obj in domain_objects:
            if obj.get("action") == ACTION_REJECT:
                continue
            archive_prefix = ""
            if obj.get("action") == ACTION_ARCHIVE:
                archive_prefix = "_archive/"
            target = dest_path / archive_prefix / obj["destination_path"]
            if not target.exists():
                continue

            source_root = _find_source_root(obj, source_sets)
            if source_root is None:
                continue
            source_file = Path(source_root) / obj["source_path"]
            if not source_file.exists():
                continue

            try:
                dest_content = target.read_bytes()
                source_content = source_file.read_bytes()
            except OSError:
                continue

            action = obj.get("action", ACTION_PRESERVE)
            if action == ACTION_PRESERVE:
                if dest_content != source_content:
                    failures.append({
                        "check": "head_content_mismatch",
                        "path": obj["destination_path"],
                        "detail": "Preserved object content differs from source",
                    })

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("history", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


# ── Gate 7: Idempotency (§8.3) ─────────────────────────────────────────────────

def idempotency_gate(manifest: dict, dest_root: str, *,
                     committer_date: str = "2026-01-01T00:00:00+0000") -> dict:
    _guard_no_production_paths(dest_root)
    checked: list[str] = [
        "rerun_same_commit_tree",
        "rerun_no_error",
    ]
    failures: list[dict] = []

    import tempfile
    rerun_dest = tempfile.mkdtemp(prefix="proof_gate_idempotency_")
    _guard_no_production_paths(rerun_dest)

    try:
        engine = RehearsalEngine(
            manifest,
            rerun_dest,
            committer_date=committer_date,
        )
        result = engine.run()
        if not result.get("invariant_holds", False):
            failures.append({
                "check": "rerun_invariant_failed",
                "detail": "Re-run rehearsal invariant does not hold",
            })
    except Exception as e:
        failures.append({
            "check": "rerun_error",
            "detail": f"Re-run rehearsal failed: {e}",
        })

    domain_groups: dict[str, list[dict]] = {}
    for obj in manifest.get("objects", []):
        dest = obj.get("destination_repo", "default")
        domain_groups.setdefault(dest, []).append(obj)

    for dest_repo in domain_groups:
        orig_dest = Path(dest_root) / dest_repo.lstrip("/")
        rerun_dest_path = Path(rerun_dest) / dest_repo.lstrip("/")

        if not orig_dest.exists() or not rerun_dest_path.exists():
            continue

        try:
            r1 = subprocess.run(
                ["git", "ls-tree", "-r", "HEAD"],
                cwd=str(orig_dest), capture_output=True, text=True, timeout=15
            )
            r2 = subprocess.run(
                ["git", "ls-tree", "-r", "HEAD"],
                cwd=str(rerun_dest_path), capture_output=True, text=True, timeout=15
            )
            if r1.returncode == 0 and r2.returncode == 0:
                lines1 = sorted(r1.stdout.strip().split("\n"))
                lines2 = sorted(r2.stdout.strip().split("\n"))
                if lines1 != lines2:
                    failures.append({
                        "check": "tree_differ",
                        "repo": dest_repo,
                        "detail": "Re-run produces different git tree",
                    })
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            failures.append({
                "check": "tree_comparison_error",
                "repo": dest_repo,
                "detail": str(e),
            })

    import shutil
    try:
        shutil.rmtree(rerun_dest)
    except OSError:
        pass

    status = "PASS" if not failures else "FAIL"
    record = _gate_record("idempotency", status, checked, failures)
    record["evidence_digest"] = _make_evidence_digest(record)
    return record


# ── Aggregate ──────────────────────────────────────────────────────────────────

def run_all_gates(manifest: dict, dest_root: str, *,
                  skip_idempotency: bool = False,
                  committer_date: str = "2026-01-01T00:00:00+0000") -> dict:
    _guard_no_production_paths(dest_root)

    gate_results = []

    gate_results.append(parity_gate(manifest, dest_root))
    gate_results.append(hash_gate(manifest, dest_root))
    gate_results.append(id_gate(manifest, dest_root))
    gate_results.append(reference_gate(manifest, dest_root))
    gate_results.append(integrity_gate(manifest, dest_root))
    gate_results.append(history_gate(manifest, dest_root))

    if not skip_idempotency:
        gate_results.append(idempotency_gate(
            manifest, dest_root, committer_date=committer_date
        ))

    all_pass = all(g["status"] == "PASS" for g in gate_results)
    failed_gates = [g["gate"] for g in gate_results if g["status"] == "FAIL"]

    return {
        "status": "PASS" if all_pass else "FAIL",
        "gates": gate_results,
        "failed_gates": failed_gates,
        "total_gates": len(gate_results),
        "passed_gates": len(gate_results) - len(failed_gates),
        "failed_gate_count": len(failed_gates),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Migration proof-gates: automated verification of rehearsal output"
    )
    ap.add_argument(
        "--manifest", required=True,
        help="Path to M3a migration manifest JSON"
    )
    ap.add_argument(
        "--dest-root", required=True,
        help="Path to M3b rehearsal destination root"
    )
    ap.add_argument(
        "--skip-idempotency", action="store_true",
        help="Skip the idempotency gate (faster, for quick checks)"
    )
    ap.add_argument(
        "--committer-date", default="2026-01-01T00:00:00+0000",
        help="Committer date for idempotency re-run (default: 2026-01-01T00:00:00+0000)"
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output report JSON file path (default: stdout)"
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    _guard_no_production_paths(args.dest_root)

    report = run_all_gates(
        manifest,
        args.dest_root,
        skip_idempotency=args.skip_idempotency,
        committer_date=args.committer_date,
    )

    report_json = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_json)
    else:
        import sys
        sys.stdout.write(report_json)


if __name__ == "__main__":
    main()
