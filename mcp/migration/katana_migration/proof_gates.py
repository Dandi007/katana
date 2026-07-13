"""Migration proof-gate suite: automated, deterministic verification of M3b rehearsal output.

Consumes M3a manifest + M3b rehearsal destination, produces structured
verification records (PASS/FAIL + evidence) per design §9.1 Migration gate
and §8.4/§8.5 contracts.  Rehearsal-only — never writes production data roots.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import unicodedata
from pathlib import Path

from katana_migration.rehearsal import (
    _extract_body_bytes,
    _is_binary,
    _is_lfs_pointer,
    _is_nfc_normalized,
    run_rehearsal,
)

ACTION_PRESERVE = "preserve"
ACTION_ID_BACKFILL = "id_backfill"
ACTION_NORMALIZE = "normalize"
ACTION_REWRITE = "rewrite"
ACTION_MERGE = "merge"
ACTION_ARCHIVE = "archive"
ACTION_REJECT = "reject"

_TRANSFORM_ACTIONS = {ACTION_ID_BACKFILL, ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE}

MAX_BASENAME_LENGTH = 255
MAX_PATH_LENGTH = 4096

PRODUCTION_ROOTS = [
    "/data/memory",
    "/data/vault/",
    "/data/wiki",
    "/data/work-records",
]


def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_hex_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _compute_evidence_digest(record: dict) -> str:
    canonical = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False)
    return _sha256_hex_str(canonical)


def _find_source_file(obj: dict, manifest: dict) -> Path | None:
    source_repo = obj.get("source_repo", "")
    source_path = obj.get("source_path", "")
    for ss in manifest.get("source_sets", []):
        if ss.get("source_repo") == source_repo:
            root = ss.get("root", "")
            if root:
                candidate = Path(root) / source_path
                if candidate.exists():
                    return candidate
    return None


def _make_verification_record(gate: str, status: str, checked: int, failures: list[dict]) -> dict:
    record = {"gate": gate, "status": status, "checked": checked, "failures": failures}
    record["evidence_digest"] = _compute_evidence_digest(record)
    return record


# ── No-production guard ────────────────────────────────────────────────────────

def _guard_no_production_paths(dest_root: str) -> None:
    dest = Path(dest_root).resolve()
    for prod_root in PRODUCTION_ROOTS:
        prod = Path(prod_root).resolve()
        try:
            dest.relative_to(prod)
            raise RuntimeError(
                f"Refusing to run proof gates on production root: {dest_root} is under {prod_root}"
            )
        except ValueError:
            pass


# ── Parity gate (§8.4) ────────────────────────────────────────────────────────

def parity_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    dest = Path(dest_root)
    objects = manifest.get("objects", [])
    summary = manifest.get("summary", {})
    checked = 0

    tracked = summary.get("tracked", len(objects))
    preserved = summary.get("preserved", 0)
    transformed = summary.get("transformed", 0)
    archived = summary.get("archived", 0)
    rejected = summary.get("rejected", 0)
    unclassified = tracked - (preserved + transformed + archived + rejected)

    checked += 1
    if unclassified != 0:
        failures.append({
            "type": "unclassified_nonzero",
            "detail": f"Unclassified objects: {unclassified}",
            "evidence": {"tracked": tracked, "preserved": preserved, "transformed": transformed,
                         "archived": archived, "rejected": rejected, "unclassified": unclassified},
        })

    checked += 1
    invariant_sum = preserved + transformed + archived + rejected
    if tracked != invariant_sum:
        failures.append({
            "type": "invariant_violation",
            "detail": f"tracked ({tracked}) != preserved ({preserved}) + transformed ({transformed}) + archived ({archived}) + rejected ({rejected})",
            "evidence": {"tracked": tracked, "sum": invariant_sum},
        })

    manifest_ids = {obj["domain_resource_id"] for obj in objects if obj.get("domain_resource_id") and obj.get("action") != ACTION_REJECT}
    dest_ids: set[str] = set()
    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue
        dest_path = obj.get("destination_path", "")
        if not dest_path:
            continue
        target = dest / obj.get("destination_repo", "").lstrip("/") / dest_path
        if target.exists():
            dest_ids.add(obj.get("domain_resource_id"))

    checked += 1
    if manifest_ids != dest_ids:
        missing = manifest_ids - dest_ids
        extra = dest_ids - manifest_ids
        if missing:
            failures.append({
                "type": "missing_objects",
                "detail": f"Manifest objects not materialized: {len(missing)}",
                "evidence": sorted(missing),
            })
        if extra:
            failures.append({
                "type": "extra_objects",
                "detail": f"Destination objects not in manifest: {len(extra)}",
                "evidence": sorted(extra),
            })

    checked += 1
    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            dest_path = obj.get("destination_path", "")
            if dest_path:
                target = dest / obj.get("destination_repo", "").lstrip("/") / dest_path
                if target.exists():
                    failures.append({
                        "type": "rejected_object_written",
                        "detail": f"Rejected object materialized: {obj.get('destination_path', '')}",
                        "evidence": str(target),
                    })

    checked += 1
    for obj in objects:
        if obj.get("action") != ACTION_REJECT:
            dest_path = obj.get("destination_path", "")
            if dest_path:
                target = dest / obj.get("destination_repo", "").lstrip("/") / dest_path
                if not target.exists():
                    failures.append({
                        "type": "silent_skip",
                        "detail": f"Manifest item not materialized: {obj.get('destination_path', '')}",
                        "evidence": str(target),
                    })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("parity", status, checked, failures)


# ── Hash reconciliation gate (§8.5) ───────────────────────────────────────────

def hash_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    dest = Path(dest_root)
    objects = manifest.get("objects", [])
    checked = 0

    for obj in objects:
        action = obj.get("action", ACTION_PRESERVE)
        if action == ACTION_REJECT:
            continue
        if action == ACTION_ARCHIVE:
            target = dest / obj.get("destination_repo", "").lstrip("/") / "_archive" / obj.get("destination_path", "")
        else:
            target = dest / obj.get("destination_repo", "").lstrip("/") / obj.get("destination_path", "")

        if not target.exists():
            continue

        content = target.read_bytes()
        actual_sha = _sha256_hex(content)

        checked += 1
        if action == ACTION_PRESERVE:
            expected_sha = obj.get("sha256") or obj.get("pre_hash")
            if expected_sha and actual_sha != expected_sha:
                failures.append({
                    "type": "preserve_hash_mismatch",
                    "detail": f"{obj.get('destination_path', '?')}: expected {expected_sha}, got {actual_sha}",
                    "evidence": {"expected": expected_sha, "actual": actual_sha},
                })

        elif action == ACTION_ID_BACKFILL:
            checked += 1
            source_file = _find_source_file(obj, manifest)
            if source_file is not None:
                source_content = source_file.read_bytes()
                orig_body = _extract_body_bytes(source_content)
                new_body = _extract_body_bytes(content)
                if new_body != orig_body:
                    failures.append({
                        "type": "id_backfill_body_altered",
                        "detail": f"{obj.get('destination_path', '?')}: body bytes changed during id_backfill",
                        "evidence": {"source_body_len": len(orig_body), "dest_body_len": len(new_body)},
                    })

        elif action in (ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE):
            if action in (ACTION_NORMALIZE, ACTION_REWRITE):
                diff_path = target.parent / f"{target.name}.diff_manifest.json"
                checked += 1
                if not diff_path.exists():
                    failures.append({
                        "type": "missing_diff_manifest",
                        "detail": f"{obj.get('destination_path', '?')} ({action}): .diff_manifest.json not found",
                        "evidence": str(diff_path),
                    })
                else:
                    try:
                        diff = json.loads(diff_path.read_text())
                        if diff.get("action") != action:
                            failures.append({
                                "type": "diff_manifest_action_mismatch",
                                "detail": f"{obj.get('destination_path', '?')}: diff_manifest.action={diff.get('action')}, expected={action}",
                                "evidence": diff,
                            })
                    except json.JSONDecodeError:
                        failures.append({
                            "type": "invalid_diff_manifest",
                            "detail": f"{obj.get('destination_path', '?')}: .diff_manifest.json is not valid JSON",
                            "evidence": str(diff_path),
                        })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("hash", status, checked, failures)


# ── ID reconciliation gate (§8.4) ──────────────────────────────────────────────

def id_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    dest = Path(dest_root)
    objects = manifest.get("objects", [])
    redirect_map = manifest.get("redirect_map", {})
    checked = 0

    all_ids = {obj["domain_resource_id"] for obj in objects if obj.get("domain_resource_id")}
    rejected_ids = {obj["domain_resource_id"] for obj in objects if obj.get("action") == ACTION_REJECT and obj.get("domain_resource_id")}

    checked += 1
    for obj in objects:
        if obj.get("action") != ACTION_REJECT and obj.get("domain_resource_id") in rejected_ids:
            failures.append({
                "type": "rejected_id_reused",
                "detail": f"ID {obj['domain_resource_id']} reused by non-rejected object {obj.get('destination_path', '?')}",
                "evidence": obj["domain_resource_id"],
            })

    checked += 1
    redirect_keys = set(redirect_map.keys())
    for obj in objects:
        if obj.get("action") == ACTION_ID_BACKFILL:
            if obj.get("source_path") not in redirect_keys:
                failures.append({
                    "type": "missing_redirect",
                    "detail": f"ID backfill object {obj.get('source_path', '?')} missing from redirect map",
                    "evidence": obj.get("source_path", ""),
                })

    checked += 1
    for obj in objects:
        if obj.get("action") == ACTION_ID_BACKFILL:
            dest_path = obj.get("destination_path", "")
            if dest_path:
                target = dest / obj.get("destination_repo", "").lstrip("/") / dest_path
                if target.exists():
                    content = target.read_bytes()
                    resource_id = obj.get("domain_resource_id", "")
                    if resource_id and resource_id.encode() not in content:
                        failures.append({
                            "type": "id_not_injected",
                            "detail": f"ID {resource_id} not found in backfilled file {obj.get('destination_path', '?')}",
                            "evidence": resource_id,
                        })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("id", status, checked, failures)


# ── Reference reconciliation gate (§8.5) ──────────────────────────────────────

def reference_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    dest = Path(dest_root)
    checked = 0

    for domain_name in {obj.get("destination_repo", "") for obj in manifest.get("objects", [])}:
        if not domain_name:
            continue
        refs_path = dest / domain_name.lstrip("/") / "references.json"
        checked += 1
        if not refs_path.exists():
            continue

        refs = json.loads(refs_path.read_text())
        entries = refs.get("entries", [])
        old_broken_ack = refs.get("old_broken_acknowledged", 0)
        new_broken = refs.get("new_broken", 0)

        checked += 1
        constraint_holds = new_broken <= old_broken_ack
        if not constraint_holds:
            failures.append({
                "type": "reference_constraint_violation",
                "detail": f"new_broken ({new_broken}) - acknowledged_old_broken ({old_broken_ack}) != 0",
                "evidence": {"new_broken": new_broken, "old_broken_acknowledged": old_broken_ack},
            })

        checked += 1
        for entry in entries:
            if entry.get("disposition") == "broken_new":
                if entry.get("old_target_id") is None:
                    failures.append({
                        "type": "broken_new_misclassified",
                        "detail": "BROKEN_NEW ref has old_target_id=None (should be resolvable before)",
                        "evidence": entry,
                    })
                if entry.get("new_target_id") is not None:
                    failures.append({
                        "type": "broken_new_misclassified",
                        "detail": "BROKEN_NEW ref has new_target_id != None (should be broken after)",
                        "evidence": entry,
                    })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("reference", status, checked, failures)


# ── Integrity gate (§8.5) ─────────────────────────────────────────────────────

def _find_git_repos(dest_root: str) -> list[Path]:
    repos: list[Path] = []
    dest = Path(dest_root)
    for dirpath, dirnames, _ in os.walk(str(dest)):
        if ".git" in dirnames:
            dirnames.remove(".git")
            repos.append(Path(dirpath))
    return repos


def integrity_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    dest = Path(dest_root)
    objects = manifest.get("objects", [])
    checked = 0

    repos = _find_git_repos(dest_root)
    for repo in repos:
        checked += 1
        result = subprocess.run(
            ["git", "fsck", "--no-dangling", "--strict"],
            cwd=str(repo), capture_output=True, text=True,
        )
        if result.returncode != 0:
            failures.append({
                "type": "git_fsck_failed",
                "detail": f"git fsck failed in {repo}: {result.stderr.strip()}",
                "evidence": {"repo": str(repo), "stderr": result.stderr.strip()},
            })

    basename_casefold: dict[str, str] = {}

    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue
        dest_path = obj.get("destination_path", "")
        if not dest_path:
            continue
        target = dest / obj.get("destination_repo", "").lstrip("/") / dest_path
        if not target.exists():
            continue

        checked += 1
        if target.is_symlink():
            failures.append({
                "type": "symlink_rejected",
                "detail": f"Symlink found: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })
            continue

        try:
            content = target.read_bytes()
        except OSError as e:
            failures.append({
                "type": "read_error",
                "detail": f"Failed to read {obj.get('destination_path', '?')}: {e}",
                "evidence": str(target),
            })
            continue

        checked += 1
        if _is_binary(content):
            failures.append({
                "type": "binary_content",
                "detail": f"Binary content: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })

        checked += 1
        if _is_lfs_pointer(content):
            failures.append({
                "type": "lfs_pointer",
                "detail": f"LFS pointer: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })

        checked += 1
        st = target.stat()
        if st.st_mode & 0o111:
            failures.append({
                "type": "executable_bit",
                "detail": f"Executable bit set: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })

        checked += 1
        basename = Path(dest_path).name
        if len(basename.encode("utf-8")) > MAX_BASENAME_LENGTH:
            failures.append({
                "type": "path_length_basename",
                "detail": f"Basename exceeds {MAX_BASENAME_LENGTH} bytes: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })

        checked += 1
        if len(dest_path.encode("utf-8")) > MAX_PATH_LENGTH:
            failures.append({
                "type": "path_length",
                "detail": f"Path exceeds {MAX_PATH_LENGTH} bytes: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })

        checked += 1
        casefold_name = basename.casefold()
        if casefold_name in basename_casefold and basename_casefold[casefold_name] != basename:
            failures.append({
                "type": "casefold_collision",
                "detail": f"Casefold collision: {basename} vs {basename_casefold[casefold_name]}",
                "evidence": {"path": dest_path, "collision_with": basename_casefold[casefold_name]},
            })
        else:
            basename_casefold[casefold_name] = basename

        checked += 1
        if not _is_nfc_normalized(content):
            failures.append({
                "type": "unicode_nfc",
                "detail": f"Not NFC normalized: {obj.get('destination_path', '?')}",
                "evidence": str(target),
            })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("integrity", status, checked, failures)


# ── History-extraction gate (§8.5) ────────────────────────────────────────────

def history_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    dest = Path(dest_root)
    objects = manifest.get("objects", [])
    checked = 0

    in_scope_paths: set[str] = set()
    for obj in objects:
        if obj.get("action") != ACTION_REJECT:
            in_scope_paths.add(obj.get("destination_path", ""))

    repos = _find_git_repos(dest_root)
    for repo in repos:
        checked += 1
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=str(repo), capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            if line == ".gitkeep":
                continue
            if line.startswith(".migration"):
                continue
            if line.startswith("_archive/"):
                continue
            if line in ("MIGRATION_BASE.json", "redirects.json", "references.json"):
                continue
            if line.endswith(".diff_manifest.json"):
                continue
            checked += 1
            if line not in in_scope_paths:
                failures.append({
                    "type": "out_of_scope_leak",
                    "detail": f"Path '{line}' in repo {repo} is not in the manifest's in-scope set",
                    "evidence": str(repo / line),
                })

    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue
        dest_path = obj.get("destination_path", "")
        if not dest_path:
            continue
        target = dest / obj.get("destination_repo", "").lstrip("/") / dest_path
        if not target.exists():
            continue

        source_file = _find_source_file(obj, manifest)
        if source_file is None:
            continue

        checked += 1
        dest_content = target.read_bytes()
        source_content = source_file.read_bytes()
        if dest_content != source_content:
            if obj.get("action") in (ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE, ACTION_ID_BACKFILL):
                continue
            if obj.get("action") == ACTION_PRESERVE:
                failures.append({
                    "type": "content_mismatch",
                    "detail": f"Preserved file {dest_path} differs from frozen source",
                    "evidence": {"dest_path": dest_path, "source_path": str(source_file)},
                })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("history", status, checked, failures)


# ── Idempotency gate (§8.3) ───────────────────────────────────────────────────

def idempotency_gate(manifest: dict, dest_root: str) -> dict:
    _guard_no_production_paths(dest_root)
    failures: list[dict] = []
    checked = 0

    dest = Path(dest_root)
    temp_base = dest.parent / f"{dest.name}_idempotency_check"
    temp_base.mkdir(parents=True, exist_ok=True)

    try:
        dest2 = temp_base / "run2"
        dest2.mkdir(parents=True, exist_ok=True)

        result2 = run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

        repos1 = _find_git_repos(dest_root)
        repos2 = _find_git_repos(str(dest2))

        checked += 1
        repo_names1 = {r.name for r in repos1}
        repo_names2 = {r.name for r in repos2}
        if repo_names1 != repo_names2:
            failures.append({
                "type": "domain_mismatch",
                "detail": f"Domain sets differ: {repo_names1} vs {repo_names2}",
                "evidence": {"run1": sorted(repo_names1), "run2": sorted(repo_names2)},
            })

        for repo1 in repos1:
            matching = [r for r in repos2 if r.name == repo1.name]
            if not matching:
                continue
            repo2 = matching[0]

            checked += 1
            result1 = subprocess.run(
                ["git", "ls-tree", "-r", "HEAD"],
                cwd=str(repo1), capture_output=True, text=True,
            )
            result2 = subprocess.run(
                ["git", "ls-tree", "-r", "HEAD"],
                cwd=str(repo2), capture_output=True, text=True,
            )
            lines1 = sorted(result1.stdout.strip().split("\n"))
            lines2 = sorted(result2.stdout.strip().split("\n"))
            if lines1 != lines2:
                failures.append({
                    "type": "tree_not_byte_identical",
                    "detail": f"Git trees differ for {repo1.name}",
                    "evidence": {"repo": repo1.name},
                })
    finally:
        import shutil
        shutil.rmtree(str(temp_base), ignore_errors=True)

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("idempotency", status, checked, failures)


# ── Verification record gate (§9.4) ───────────────────────────────────────────

_REQUIRED_RECORD_KEYS = {"gate", "status", "checked", "failures", "evidence_digest"}
_VALID_STATUSES = {"PASS", "FAIL"}


def verification_record_gate(records: list[dict]) -> dict:
    failures: list[dict] = []
    checked = len(records)

    for i, record in enumerate(records):
        if not isinstance(record, dict):
            failures.append({
                "type": "invalid_type",
                "detail": f"Record {i} is not a dict",
                "evidence": str(type(record)),
            })
            continue

        missing = _REQUIRED_RECORD_KEYS - set(record.keys())
        if missing:
            failures.append({
                "type": "missing_keys",
                "detail": f"Record {i} ('{record.get('gate', '?')}') missing keys: {missing}",
                "evidence": sorted(missing),
            })

        if record.get("status") not in _VALID_STATUSES:
            failures.append({
                "type": "invalid_status",
                "detail": f"Record {i} ('{record.get('gate', '?')}') has invalid status: {record.get('status')}",
                "evidence": record.get("status"),
            })

        if not isinstance(record.get("failures"), list):
            failures.append({
                "type": "invalid_failures",
                "detail": f"Record {i} ('{record.get('gate', '?')}') failures is not a list",
                "evidence": str(type(record.get("failures"))),
            })

        if isinstance(record.get("checked"), bool) or not isinstance(record.get("checked"), (int, float)):
            failures.append({
                "type": "invalid_checked",
                "detail": f"Record {i} ('{record.get('gate', '?')}') checked is not a number",
                "evidence": str(record.get("checked")),
            })

        if record.get("evidence_digest"):
            recomputed = _compute_evidence_digest({k: v for k, v in record.items() if k != "evidence_digest"})
            if recomputed != record["evidence_digest"]:
                failures.append({
                    "type": "evidence_digest_mismatch",
                    "detail": f"Record {i} ('{record.get('gate', '?')}') evidence_digest does not match recomputed",
                    "evidence": {"stored": record["evidence_digest"], "recomputed": recomputed},
                })

    status = "PASS" if not failures else "FAIL"
    return _make_verification_record("verification_record", status, checked, failures)


# ── Aggregate report ──────────────────────────────────────────────────────────

def run_all_gates(manifest: dict, dest_root: str) -> dict:
    records: list[dict] = []

    records.append(parity_gate(manifest, dest_root))
    records.append(hash_gate(manifest, dest_root))
    records.append(id_gate(manifest, dest_root))
    records.append(reference_gate(manifest, dest_root))
    records.append(integrity_gate(manifest, dest_root))
    records.append(history_gate(manifest, dest_root))
    records.append(idempotency_gate(manifest, dest_root))

    vrec = verification_record_gate(records)
    records.append(vrec)

    overall = "PASS" if all(r["status"] == "PASS" for r in records) else "FAIL"

    report = {
        "overall": overall,
        "gates": records,
    }
    report["evidence_digest"] = _compute_evidence_digest(report)
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import sys

    ap = argparse.ArgumentParser(
        description="Migration proof gate suite: automated verification of M3b rehearsal output"
    )
    ap.add_argument(
        "--manifest", required=True,
        help="Path to M3a manifest JSON file"
    )
    ap.add_argument(
        "--dest-root", required=True,
        help="Path to M3b rehearsal destination root"
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output report JSON file path (default: stdout)"
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    report = run_all_gates(manifest, args.dest_root)

    report_json = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_json)
    else:
        sys.stdout.write(report_json)


if __name__ == "__main__":
    main()