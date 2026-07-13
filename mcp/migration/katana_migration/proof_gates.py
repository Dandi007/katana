"""Migration proof-gate suite: automated, repeatable, structured-evidence verification.

Design §9.1 Migration gate + §8.4/§8.5 contracts.  Each gate consumes a
M3a manifest and a M3b rehearsal destination (commit-pinned) and produces a
structured verification record (PASS/FAIL + evidence).  Deterministic:
same input → equivalent record (sorted-key evidence digests).

Non-goals (hard boundary): no production writes, no FROZEN/cutover/fencing,
no §9.2/§9.3, no remote push.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path

# ── Re-use from rehearsal / inventory ─────────────────────────────────────────

from katana_migration.rehearsal import (
    ACTION_ARCHIVE,
    ACTION_ID_BACKFILL,
    ACTION_MERGE,
    ACTION_NORMALIZE,
    ACTION_PRESERVE,
    ACTION_REJECT,
    ACTION_REWRITE,
    DISPOSITION_BROKEN_NEW,
    DISPOSITION_BROKEN_OLD_ACK,
    DISPOSITION_REDIRECTED,
    DISPOSITION_RESOLVED,
    RehearsalError,
    run_rehearsal,
    _extract_body_bytes,
    _is_binary,
    _is_lfs_pointer,
    _is_nfc_normalized,
    _sha256_hex,
)

_TRANSFORM_ACTIONS = {ACTION_ID_BACKFILL, ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE}

# ── Production-root guard ─────────────────────────────────────────────────────

PRODUCTION_ROOTS = [
    "/data/memory",
    "/data/vault/",
    "/data/wiki",
    "/data/work-records",
]


def _guard_no_production_paths(dest_root: str) -> None:
    """Fail-closed: any path under a production data root raises."""
    resolved = str(Path(dest_root).resolve())
    for prod in PRODUCTION_ROOTS:
        prod_resolved = str(Path(prod).resolve()).rstrip("/")
        if resolved == prod_resolved or resolved.startswith(prod_resolved + "/"):
            raise RuntimeError(
                f"Production-root guard: dest_root '{dest_root}' resolves under "
                f"production path '{prod}'. Refusing to operate on live data."
            )


# ── Evidence helpers ──────────────────────────────────────────────────────────

def _evidence_digest(evidence: dict) -> str:
    """Deterministic SHA-256 of sorted-key JSON evidence."""
    raw = json.dumps(evidence, indent=2, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _make_record(gate: str, status: str, checked: list[str], failures: list[dict]) -> dict:
    evidence = {
        "gate": gate,
        "status": status,
        "checked": sorted(checked),
        "failures": sorted(failures, key=lambda f: json.dumps(f, sort_keys=True)),
    }
    return {**evidence, "evidence_digest": _evidence_digest(evidence)}


# ── Domain repo helpers ───────────────────────────────────────────────────────

def _domain_repo_paths(manifest: dict, dest_root: str) -> dict[str, Path]:
    dest = Path(dest_root)
    domain_repos: dict[str, str] = {}
    for obj in manifest.get("objects", []):
        dr = obj.get("destination_repo", "default")
        domain_repos[dr] = dr
    return {dr: dest / dr.lstrip("/") for dr in sorted(domain_repos)}


def _all_domain_dest_paths(manifest: dict, dest_root: str) -> list[Path]:
    return list(_domain_repo_paths(manifest, dest_root).values())


# ── Gate: Parity (§8.4) ──────────────────────────────────────────────────────

def parity_gate(manifest: dict, dest_root: str) -> dict:
    """Verify tracked = preserved + transformed + archived + rejected,
    unclassified = 0, destination ↔ manifest 1:1, zero silent skip."""
    checked: list[str] = []
    failures: list[dict] = []

    summary = manifest.get("summary", {})
    objects = manifest.get("objects", [])

    tracked = summary.get("tracked", len(objects))
    preserved = summary.get("preserved", 0)
    transformed = summary.get("transformed", 0)
    archived = summary.get("archived", 0)
    rejected = summary.get("rejected", 0)
    unclassified = summary.get("unclassified", 0)

    computed = preserved + transformed + archived + rejected
    checked.append("invariant:tracked==preserved+transformed+archived+rejected")
    if tracked != computed:
        failures.append({
            "check": "invariant",
            "expected": tracked,
            "actual": computed,
            "detail": f"tracked={tracked} != p{preserved}+t{transformed}+a{archived}+r{rejected}={computed}",
        })

    checked.append("unclassified==0")
    if unclassified != 0:
        failures.append({
            "check": "unclassified",
            "expected": 0,
            "actual": unclassified,
            "detail": f"{unclassified} objects unclassified",
        })

    visited_paths: set[str] = set()
    missing: list[dict] = []
    extra: list[dict] = []
    rejected_materialized: list[dict] = []

    for obj in objects:
        action = obj.get("action", ACTION_PRESERVE)
        dest_path = obj.get("destination_path", "")
        dest_repo = obj.get("destination_repo", "default")

        if action == ACTION_REJECT:
            full = Path(dest_root) / dest_repo.lstrip("/") / dest_path
            if full.exists():
                rejected_materialized.append({
                    "path": dest_path,
                    "repo": dest_repo,
                    "detail": "Rejected object materialized at destination",
                })
            continue

        full = Path(dest_root) / dest_repo.lstrip("/") / dest_path
        if action == ACTION_ARCHIVE:
            full = Path(dest_root) / dest_repo.lstrip("/") / "_archive" / dest_path

        visited_paths.add(dest_path)
        if not full.exists():
            missing.append({
                "path": dest_path,
                "repo": dest_repo,
                "action": action,
                "detail": f"Manifest object not materialized: {dest_path}",
            })

    checked.append("manifest↔destination:1:1")
    if missing:
        failures.append({
            "check": "missing_objects",
            "count": len(missing),
            "entries": missing,
        })

    if rejected_materialized:
        failures.append({
            "check": "rejected_materialized",
            "count": len(rejected_materialized),
            "entries": rejected_materialized,
        })

    for domain_repo, repo_path in _domain_repo_paths(manifest, dest_root).items():
        if not repo_path.exists():
            continue
        for root, dirs, files in os.walk(str(repo_path)):
            if ".git" in dirs:
                dirs.remove(".git")
            for fname in files:
                rel = str(Path(root).relative_to(repo_path) / fname)
                if rel.startswith(".migration") or rel.startswith("_archive/") or rel == ".gitkeep":
                    continue
                if rel in ("MIGRATION_BASE.json", "references.json", "redirects.json"):
                    continue
                if rel.endswith(".diff_manifest.json"):
                    continue
                if rel not in visited_paths:
                    extra.append({
                        "path": rel,
                        "repo": domain_repo,
                        "detail": "Extra object in destination not in manifest",
                    })

    checked.append("zero_silent_skip")
    if extra:
        failures.append({
            "check": "extra_objects",
            "count": len(extra),
            "entries": extra,
        })

    status = "PASS" if not failures else "FAIL"
    return _make_record("parity", status, checked, failures)


# ── Gate: Hash reconciliation (§8.5) ──────────────────────────────────────────

def hash_gate(manifest: dict, dest_root: str) -> dict:
    """Verify SHA-256 byte-equality for preserve/raw, id_backfill body
    preservation, and normalize/rewrite/merge pre_hash→post_hash."""
    checked: list[str] = []
    failures: list[dict] = []

    objects = manifest.get("objects", [])

    for obj in objects:
        action = obj.get("action", ACTION_PRESERVE)
        if action == ACTION_REJECT:
            continue

        dest_repo = obj.get("destination_repo", "default")
        dest_path = obj.get("destination_path", "")

        if action == ACTION_ARCHIVE:
            full = Path(dest_root) / dest_repo.lstrip("/") / "_archive" / dest_path
        else:
            full = Path(dest_root) / dest_repo.lstrip("/") / dest_path

        if not full.exists():
            continue

        try:
            content = full.read_bytes()
        except OSError:
            failures.append({
                "check": "read_error",
                "path": dest_path,
                "detail": f"Cannot read destination file: {full}",
            })
            continue

        actual_sha = _sha256_hex(content)

        if action == ACTION_PRESERVE:
            checked.append("preserve:sha256_byte_equal")
            expected_sha = obj.get("sha256") or obj.get("pre_hash")
            if expected_sha and actual_sha != expected_sha:
                failures.append({
                    "check": "preserve_sha256_mismatch",
                    "path": dest_path,
                    "expected": expected_sha,
                    "actual": actual_sha,
                    "detail": f"Preserve object {dest_path} hash mismatch",
                })

        elif action == ACTION_ID_BACKFILL:
            checked.append("id_backfill:body_bytes_preserved")
            source_root = _find_source_root(obj, manifest)
            if source_root:
                source_file = Path(source_root) / obj["source_path"]
                if source_file.exists():
                    source_content = source_file.read_bytes()
                    source_body = _extract_body_bytes(source_content)
                    dest_body = _extract_body_bytes(content)
                    if source_body != dest_body:
                        failures.append({
                            "check": "id_backfill_body_altered",
                            "path": dest_path,
                            "detail": "ID backfill altered body bytes",
                        })
                    if obj.get("domain_resource_id"):
                        if obj["domain_resource_id"].encode() not in content:
                            failures.append({
                                "check": "id_backfill_id_not_injected",
                                "path": dest_path,
                                "detail": f"ID {obj['domain_resource_id']} not found in destination",
                            })

        elif action in (ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE):
            checked.append(f"{action}:pre_hash→post_hash")
            post_hash = obj.get("post_hash")
            if post_hash:
                if actual_sha != post_hash:
                    failures.append({
                        "check": f"{action}_post_hash_mismatch",
                        "path": dest_path,
                        "expected": post_hash,
                        "actual": actual_sha,
                        "detail": f"{action} object {dest_path} post_hash mismatch",
                    })

            if action in (ACTION_NORMALIZE, ACTION_REWRITE):
                diff_path = full.parent / f"{full.name}.diff_manifest.json"
                if not diff_path.exists():
                    failures.append({
                        "check": f"{action}_missing_diff_manifest",
                        "path": dest_path,
                        "detail": f"Missing diff_manifest.json for {action}",
                    })
                else:
                    try:
                        diff = json.loads(diff_path.read_text())
                        if diff.get("action") != action:
                            failures.append({
                                "check": f"{action}_diff_manifest_action_mismatch",
                                "path": dest_path,
                                "expected": action,
                                "actual": diff.get("action"),
                                "detail": "diff_manifest.json action mismatch",
                            })
                    except json.JSONDecodeError:
                        failures.append({
                            "check": f"{action}_diff_manifest_parse_error",
                            "path": dest_path,
                            "detail": "Cannot parse diff_manifest.json",
                        })

    status = "PASS" if not failures else "FAIL"
    return _make_record("hash", status, checked, failures)


def _find_source_root(obj: dict, manifest: dict) -> str | None:
    source_repo = obj.get("source_repo", "")
    source_path = obj.get("source_path", "")
    candidates = []
    for i, ss in enumerate(manifest.get("source_sets", [])):
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


# ── Gate: ID reconciliation (§8.4) ────────────────────────────────────────────

def id_gate(manifest: dict, dest_root: str) -> dict:
    """Verify canonical Memory ID byte preservation, legacy/stable ID
    consistency, rejected-ID non-reuse, redirect map completeness."""
    checked: list[str] = []
    failures: list[dict] = []

    objects = manifest.get("objects", [])
    redirect_map = manifest.get("redirect_map", {})

    all_ids: set[str] = set()
    rejected_ids: set[str] = set()

    for obj in objects:
        resource_id = obj.get("domain_resource_id")
        action = obj.get("action", ACTION_PRESERVE)

        if resource_id and action == ACTION_REJECT:
            rejected_ids.add(resource_id)
            continue

        if resource_id:
            if resource_id in all_ids:
                failures.append({
                    "check": "duplicate_id",
                    "id": resource_id,
                    "path": obj.get("destination_path", ""),
                    "detail": f"ID {resource_id} appears more than once in non-rejected objects",
                })
            all_ids.add(resource_id)

    checked.append("rejected_id_not_reused")
    for rid in rejected_ids:
        if rid in all_ids:
            failures.append({
                "check": "rejected_id_reused",
                "id": rid,
                "detail": f"Rejected ID {rid} reused in non-rejected object",
            })

    checked.append("id_backfill_injection")
    checked.append("redirect_map_completeness")
    for obj in objects:
        action = obj.get("action", ACTION_PRESERVE)
        if action == ACTION_REJECT:
            continue

        resource_id = obj.get("domain_resource_id")
        if not resource_id:
            continue

        dest_repo = obj.get("destination_repo", "default")
        dest_path = obj.get("destination_path", "")

        if action == ACTION_ARCHIVE:
            full = Path(dest_root) / dest_repo.lstrip("/") / "_archive" / dest_path
        else:
            full = Path(dest_root) / dest_repo.lstrip("/") / dest_path

        if not full.exists():
            continue

        try:
            content = full.read_bytes()
        except OSError:
            continue

        if action == ACTION_ID_BACKFILL:
            if resource_id.encode() not in content:
                failures.append({
                    "check": "id_not_injected",
                    "path": dest_path,
                    "id": resource_id,
                    "detail": f"ID {resource_id} not injected into {dest_path}",
                })

        if action in (ACTION_PRESERVE, ACTION_NORMALIZE, ACTION_REWRITE):
            object_class = obj.get("object_class", "")
            if object_class.startswith("memory_"):
                checked.append("canonical_id_byte_preserved")
                if resource_id.encode() not in content:
                    failures.append({
                        "check": "canonical_id_missing",
                        "path": dest_path,
                        "id": resource_id,
                        "detail": f"Canonical ID {resource_id} not found in destination",
                    })

    for obj in objects:
        if obj.get("action") == ACTION_ID_BACKFILL and obj.get("domain_resource_id"):
            src_path = obj.get("source_path", "")
            if src_path not in redirect_map:
                failures.append({
                    "check": "redirect_map_incomplete",
                    "path": src_path,
                    "detail": f"id_backfill object {src_path} missing from redirect_map",
                })
            elif redirect_map[src_path] != obj["domain_resource_id"]:
                failures.append({
                    "check": "redirect_map_mismatch",
                    "path": src_path,
                    "expected": redirect_map[src_path],
                    "actual": obj["domain_resource_id"],
                    "detail": "redirect_map entry mismatch",
                })

    status = "PASS" if not failures else "FAIL"
    return _make_record("id", status, checked, failures)


# ── Gate: Reference reconciliation (§8.5) ─────────────────────────────────────

def reference_gate(manifest: dict, dest_root: str) -> dict:
    """Verify reference manifest completeness, new_broken - acked_old_broken == 0,
    and pre-resolvable references still point to same target ID."""
    checked: list[str] = []
    failures: list[dict] = []

    for domain_repo, repo_path in _domain_repo_paths(manifest, dest_root).items():
        refs_path = repo_path / "references.json"
        if not refs_path.exists():
            continue

        try:
            refs = json.loads(refs_path.read_text())
        except (json.JSONDecodeError, OSError):
            failures.append({
                "check": "references_json_parse",
                "repo": domain_repo,
                "detail": "Cannot parse references.json",
            })
            continue

        checked.append("reference_manifest_complete")
        entries = refs.get("entries", [])
        if not entries:
            continue

        required_fields = {"source_id", "old_literal", "old_target_id", "new_target_id", "anchor", "disposition"}
        for i, e in enumerate(entries):
            missing = required_fields - set(e.keys())
            if missing:
                failures.append({
                    "check": "reference_missing_fields",
                    "index": i,
                    "missing": sorted(missing),
                    "detail": f"Reference entry {i} missing fields: {missing}",
                })

        old_broken_ack = refs.get("old_broken_acknowledged", 0)
        new_broken = refs.get("new_broken", 0)

        checked.append("new_broken−acknowledged_old_broken==0")
        if new_broken != 0:
            failures.append({
                "check": "reference_constraint_violation",
                "old_broken_acknowledged": old_broken_ack,
                "new_broken": new_broken,
                "detail": f"new_broken({new_broken}) != 0, new broken references introduced",
            })

        checked.append("pre_resolvable→same_target_id")
        for e in entries:
            disp = e.get("disposition")
            if disp == DISPOSITION_RESOLVED:
                if e.get("old_target_id") != e.get("new_target_id"):
                    failures.append({
                        "check": "resolved_target_id_changed",
                        "source_id": e.get("source_id"),
                        "old_target_id": e.get("old_target_id"),
                        "new_target_id": e.get("new_target_id"),
                        "detail": "Resolved reference changed target ID",
                    })
            elif disp == DISPOSITION_BROKEN_NEW:
                if e.get("old_target_id") is None:
                    failures.append({
                        "check": "broken_new_without_old_target",
                        "source_id": e.get("source_id"),
                        "detail": "BROKEN_NEW reference has no old_target_id",
                    })
                if e.get("new_target_id") is not None:
                    failures.append({
                        "check": "broken_new_with_new_target",
                        "source_id": e.get("source_id"),
                        "detail": "BROKEN_NEW reference has a non-None new_target_id",
                    })

    status = "PASS" if not failures else "FAIL"
    return _make_record("reference", status, checked, failures)


# ── Gate: Integrity (§8.5) ────────────────────────────────────────────────────

def integrity_gate(manifest: dict, dest_root: str) -> dict:
    """Verify git fsck, LFS, executable, binary, NFC, casefold, path length,
    symlink across all destination repos."""
    checked: list[str] = []
    failures: list[dict] = []

    for domain_repo, repo_path in _domain_repo_paths(manifest, dest_root).items():
        if not repo_path.exists():
            continue

        git_dir = repo_path / ".git"
        if git_dir.exists():
            checked.append("git_fsck")
            try:
                result = subprocess.run(
                    ["git", "-C", str(repo_path), "fsck", "--no-dangling"],
                    check=False, capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    failures.append({
                        "check": "git_fsck",
                        "repo": domain_repo,
                        "stderr": result.stderr.strip()[:2000],
                        "detail": "git fsck failed",
                    })
            except (subprocess.TimeoutExpired, OSError) as e:
                failures.append({
                    "check": "git_fsck",
                    "repo": domain_repo,
                    "detail": f"git fsck error: {e}",
                })

        objects = [o for o in manifest.get("objects", [])
                   if o.get("destination_repo", "default") == domain_repo
                   and o.get("action") != ACTION_REJECT]

        basename_casefold: dict[str, str] = {}

        for obj in objects:
            dest_path = obj.get("destination_path", "")
            if obj.get("action") == ACTION_ARCHIVE:
                full = repo_path / "_archive" / dest_path
            else:
                full = repo_path / dest_path

            if not full.exists():
                continue

            if full.is_symlink():
                failures.append({
                    "check": "symlink_rejected",
                    "path": dest_path,
                    "repo": domain_repo,
                    "detail": "Symlink found in destination",
                })
                continue

            try:
                content = full.read_bytes()
            except OSError as e:
                failures.append({
                    "check": "read_error",
                    "path": dest_path,
                    "detail": str(e),
                })
                continue

            if _is_binary(content):
                checked.append("binary_content")
                failures.append({
                    "check": "binary_content",
                    "path": dest_path,
                    "repo": domain_repo,
                    "detail": "File contains binary bytes",
                })

            if _is_lfs_pointer(content):
                checked.append("lfs_pointer")
                failures.append({
                    "check": "lfs_pointer",
                    "path": dest_path,
                    "repo": domain_repo,
                    "detail": "File is a git LFS pointer",
                })

            st = full.stat()
            if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                checked.append("executable_bit")
                failures.append({
                    "check": "executable_bit",
                    "path": dest_path,
                    "repo": domain_repo,
                    "detail": "File has executable bit set",
                })

            basename = Path(dest_path).name
            if len(basename.encode("utf-8")) > 255:
                checked.append("path_length")
                failures.append({
                    "check": "path_length",
                    "path": dest_path,
                    "detail": f"Basename exceeds 255 bytes",
                })
            if len(dest_path.encode("utf-8")) > 4096:
                checked.append("path_length")
                failures.append({
                    "check": "path_length",
                    "path": dest_path,
                    "detail": f"Path exceeds 4096 bytes",
                })

            casefold_name = basename.casefold()
            if casefold_name in basename_casefold and basename_casefold[casefold_name] != basename:
                checked.append("casefold_collision")
                failures.append({
                    "check": "casefold_collision",
                    "path": dest_path,
                    "other": basename_casefold[casefold_name],
                    "detail": f"Casefold collision with {basename_casefold[casefold_name]}",
                })
            else:
                basename_casefold[casefold_name] = basename

            if not _is_nfc_normalized(content):
                checked.append("unicode_nfc")
                failures.append({
                    "check": "unicode_nfc",
                    "path": dest_path,
                    "repo": domain_repo,
                    "detail": "Content is not NFC normalized",
                })

    status = "PASS" if not failures else "FAIL"
    return _make_record("integrity", status, checked, failures)


# ── Gate: History-extraction correctness (§8.5) ───────────────────────────────

def history_gate(manifest: dict, dest_root: str) -> dict:
    """Verify path-filtered history only contains in-scope paths, and
    filtered head content matches frozen source for preserved objects."""
    checked: list[str] = []
    failures: list[dict] = []

    objects = manifest.get("objects", [])
    in_scope: set[str] = set()
    for obj in objects:
        in_scope.add(obj.get("destination_path", ""))
        parts = Path(obj.get("destination_path", "")).parts
        for i in range(1, len(parts)):
            in_scope.add(str(Path(*parts[:i])))

    for domain_repo, repo_path in _domain_repo_paths(manifest, dest_root).items():
        if not repo_path.exists() or not (repo_path / ".git").exists():
            continue

        in_scope_repo = {p for p in in_scope if any(
            o.get("destination_repo", "default") == domain_repo
            for o in objects if o.get("destination_path") == p
        )}

        try:
            log_result = subprocess.run(
                ["git", "-C", str(repo_path), "log", "--all", "--name-only", "--format=%H"],
                check=True, capture_output=True, text=True, timeout=30,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
            failures.append({
                "check": "history_walk_error",
                "repo": domain_repo,
                "detail": str(e),
            })
            continue

        checked.append("path-filtered_history")
        current_commit = None
        for line in log_result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(line) == 40 and all(c in "0123456789abcdef" for c in line):
                current_commit = line
                continue
            if line in (".gitkeep", "MIGRATION_BASE.json", "references.json", "redirects.json"):
                continue
            if line.startswith(".migration") or line.startswith("_archive/"):
                continue
            if line.endswith(".diff_manifest.json"):
                continue
            if line not in in_scope_repo and line not in in_scope:
                failures.append({
                    "check": "out_of_scope_leak",
                    "repo": domain_repo,
                    "commit": current_commit,
                    "path": line,
                    "detail": f"Out-of-scope path '{line}' leaked into filtered history",
                })

        checked.append("filtered_head_content_matches_source")
        for obj in objects:
            if obj.get("destination_repo", "default") != domain_repo:
                continue
            action = obj.get("action", ACTION_PRESERVE)
            if action not in (ACTION_PRESERVE, ACTION_ID_BACKFILL):
                continue

            dest_path = obj.get("destination_path", "")
            if action == ACTION_ARCHIVE:
                full = repo_path / "_archive" / dest_path
            else:
                full = repo_path / dest_path

            if not full.exists():
                continue

            source_root = _find_source_root(obj, manifest)
            if source_root is None:
                continue

            source_file = Path(source_root) / obj["source_path"]
            if not source_file.exists():
                continue

            try:
                dest_content = full.read_bytes()
                source_content = source_file.read_bytes()
            except OSError:
                continue

            if action == ACTION_PRESERVE:
                if dest_content != source_content:
                    failures.append({
                        "check": "content_mismatch",
                        "path": dest_path,
                        "repo": domain_repo,
                        "detail": "Preserved file content differs from source",
                    })
            elif action == ACTION_ID_BACKFILL:
                dest_body = _extract_body_bytes(dest_content)
                source_body = _extract_body_bytes(source_content)
                if dest_body != source_body:
                    failures.append({
                        "check": "content_mismatch",
                        "path": dest_path,
                        "repo": domain_repo,
                        "detail": "ID backfill body bytes differ from source",
                    })

    status = "PASS" if not failures else "FAIL"
    return _make_record("history", status, checked, failures)


# ── Gate: Idempotency (§8.3) ─────────────────────────────────────────────────

def idempotency_gate(manifest: dict, dest_root: str, *, second_dest_root: str | None = None) -> dict:
    """Re-run rehearsal on a second destination and assert byte-identical
    trees (stable commit).  The gate runs the rehearsal twice on fresh
    temporary directories, then compares the trees."""
    checked: list[str] = []
    failures: list[dict] = []

    import tempfile

    first_dest = tempfile.mkdtemp(prefix="idempotency-gate-run1-")
    if second_dest_root is None:
        second_dest_root = tempfile.mkdtemp(prefix="idempotency-gate-run2-")

    committer_date = "2026-01-01T00:00:00+0000"

    try:
        result1 = run_rehearsal(manifest, first_dest, committer_date=committer_date)
    except RehearsalError as e:
        failures.append({
            "check": "run1_failed",
            "detail": f"First rehearsal run failed: {e}",
        })
        return _make_record("idempotency", "FAIL", checked, failures)

    try:
        result2 = run_rehearsal(manifest, second_dest_root, committer_date=committer_date)
    except Exception as e:
        failures.append({
            "check": "idempotency_rerun_failed",
            "detail": f"Second rehearsal run failed: {e}",
        })
        return _make_record("idempotency", "FAIL", checked, failures)

    checked.append("commit_sha_identical")
    checked.append("tree_byte_identical")

    for domain_name in result1.get("domain_results", {}):
        dr1 = result1["domain_results"][domain_name]
        dr2 = result2["domain_results"].get(domain_name, {})

        if dr1.get("final_commit") != dr2.get("final_commit"):
            failures.append({
                "check": "commit_sha_differ",
                "domain": domain_name,
                "commit1": dr1.get("final_commit"),
                "commit2": dr2.get("final_commit"),
                "detail": f"Commit SHAs differ for {domain_name}",
            })

        dest1 = Path(first_dest) / domain_name.lstrip("/")
        dest2 = Path(second_dest_root) / domain_name.lstrip("/")

        if dest1.exists() and dest2.exists():
            try:
                tree1 = subprocess.run(
                    ["git", "-C", str(dest1), "ls-tree", "-r", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
                tree2 = subprocess.run(
                    ["git", "-C", str(dest2), "ls-tree", "-r", "HEAD"],
                    check=True, capture_output=True, text=True,
                ).stdout.strip()
                if tree1 != tree2:
                    failures.append({
                        "check": "tree_differ",
                        "domain": domain_name,
                        "detail": f"Git trees differ for {domain_name}",
                    })
            except subprocess.CalledProcessError as e:
                failures.append({
                    "check": "tree_compare_error",
                    "domain": domain_name,
                    "detail": str(e),
                })

    status = "PASS" if not failures else "FAIL"
    return _make_record("idempotency", status, checked, failures)


# ── Aggregate ─────────────────────────────────────────────────────────────────

def run_all_gates(manifest: dict, dest_root: str, *, second_dest_root: str | None = None) -> dict:
    """Run all proof gates and produce an aggregate verification record."""
    _guard_no_production_paths(dest_root)

    gate_records = [
        parity_gate(manifest, dest_root),
        hash_gate(manifest, dest_root),
        id_gate(manifest, dest_root),
        reference_gate(manifest, dest_root),
        integrity_gate(manifest, dest_root),
        history_gate(manifest, dest_root),
        idempotency_gate(manifest, dest_root, second_dest_root=second_dest_root),
    ]

    overall = "PASS" if all(g["status"] == "PASS" for g in gate_records) else "FAIL"

    report = {
        "overall": overall,
        "gates": gate_records,
    }
    report["evidence_digest"] = _evidence_digest(report)
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Migration proof-gate suite: automated verification of rehearsal output"
    )
    ap.add_argument(
        "--manifest", required=True,
        help="Path to M3a manifest JSON file",
    )
    ap.add_argument(
        "--dest-root", required=True,
        help="Path to M3b rehearsal destination root",
    )
    ap.add_argument(
        "--second-dest-root", default=None,
        help="Optional second destination root for idempotency gate",
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output verification record JSON file (default: stdout)",
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    report = run_all_gates(manifest, args.dest_root, second_dest_root=args.second_dest_root)

    report_json = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_json)
    else:
        sys.stdout.write(report_json)


if __name__ == "__main__":
    main()