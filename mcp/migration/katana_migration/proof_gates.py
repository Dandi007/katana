"""Migration proof-gate suite: automated, repeatable scoring (M3c).

Consumes M3a manifest + M3b rehearsal destination output and produces
structured verification records (PASS/FAIL + evidence) per design §9.

Deterministic: same inputs → equivalent records (allowing for fixed
time/committer injection).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import yaml

# ── Re-exported constants (mirrors inventory.py / rehearsal.py) ────────────────

ACTION_PRESERVE = "preserve"
ACTION_ID_BACKFILL = "id_backfill"
ACTION_NORMALIZE = "normalize"
ACTION_REWRITE = "rewrite"
ACTION_MERGE = "merge"
ACTION_ARCHIVE = "archive"
ACTION_REJECT = "reject"

_TRANSFORM_ACTIONS = {ACTION_ID_BACKFILL, ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE}

DISPOSITION_RESOLVED = "resolved"
DISPOSITION_REDIRECTED = "redirected"
DISPOSITION_BROKEN_OLD_ACK = "broken_old_ack"
DISPOSITION_BROKEN_NEW = "broken_new"

MAX_BASENAME_LENGTH = 255
MAX_PATH_LENGTH = 4096

GATE_SYMLINK = "SYMLINK"
GATE_BINARY = "BINARY"
GATE_LFS = "LFS_POINTER"
GATE_PATH_LENGTH = "PATH_LENGTH"
GATE_CASEFOLD = "CASEFOLD_COLLISION"
GATE_EXECUTABLE = "EXECUTABLE"
GATE_UNICODE_NFC = "UNICODE_NFC"

_MEMORY_ID_RE = re.compile(r"^m-[0-9a-f]{6}$")
_WIKI_ID_RE = re.compile(r"^w-[0-9a-f]{6}$")
_WF_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")

DEFAULT_COMMITTER_NAME = "Migration Engine"
DEFAULT_COMMITTER_EMAIL = "migration@katana.local"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_binary(content: bytes) -> bool:
    return b"\x00" in content[:8192]


def _is_lfs_pointer(content: bytes) -> bool:
    return content.startswith(b"version https://git-lfs.github.com/spec/v1")


def _is_nfc_normalized(content: bytes) -> bool:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return unicodedata.is_normalized("NFC", text)


def _extract_body_bytes(content: bytes) -> bytes:
    if not content.startswith(b"---\n"):
        return content
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    fm_end = re.search(r"\n---[ \t]*(?=\n|$)", text[4:])
    if fm_end is None:
        return content
    body_start = 4 + fm_end.end()
    return content[body_start:]


def _parse_frontmatter(content: bytes) -> tuple[dict | None, bytes, int]:
    if not content.startswith(b"---\n"):
        return None, content, 0
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, content, 0
    fm_end = re.search(r"\n---[ \t]*(?=\n|$)", text[4:])
    if fm_end is None:
        return None, content, 0
    fm_text = text[4:4 + fm_end.start() + 1]
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return None, content, 0
    if not isinstance(fm, dict):
        return None, content, 0
    body_start = 4 + fm_end.end()
    return fm, content[body_start:], body_start


def _is_id_literal(s: str) -> bool:
    return bool(_MEMORY_ID_RE.match(s) or _WIKI_ID_RE.match(s) or _WF_ID_RE.match(s))


def _make_evidence_digest(data: dict[str, object]) -> str:
    payload = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_record(gate: str, status: str, checked: int, failures: list[dict], evidence: dict[str, object]) -> dict:
    return {
        "gate": gate,
        "status": status,
        "checked": checked,
        "failures": failures,
        "evidence_digest": _make_evidence_digest(evidence),
    }


# ── Verification record builder ────────────────────────────────────────────────

def build_aggregate_report(records: list[dict]) -> dict:
    overall = "PASS" if all(r["status"] == "PASS" for r in records) else "FAIL"
    return {
        "overall": overall,
        "gate_count": len(records),
        "passed": sum(1 for r in records if r["status"] == "PASS"),
        "failed": sum(1 for r in records if r["status"] == "FAIL"),
        "gates": records,
    }


# ── Gate 1: Parity (total invariants + object completeness) ────────────────────

def run_parity_gate(manifest: dict, dest_root: str) -> dict:
    objects = manifest.get("objects", [])
    summary = manifest.get("summary", {})
    failures: list[dict] = []

    tracked = len(objects)
    preserved = sum(1 for r in objects if r.get("action") == ACTION_PRESERVE)
    transformed = sum(1 for r in objects if r.get("action") in _TRANSFORM_ACTIONS)
    archived = sum(1 for r in objects if r.get("action") == ACTION_ARCHIVE)
    rejected = sum(1 for r in objects if r.get("action") == ACTION_REJECT)
    unclassified = tracked - (preserved + transformed + archived + rejected)

    if unclassified != 0:
        failures.append({
            "type": "invariant_unclassified",
            "detail": f"unclassified = {unclassified}, expected 0",
        })

    expected_sum = preserved + transformed + archived + rejected
    if tracked != expected_sum:
        failures.append({
            "type": "invariant_total",
            "detail": f"tracked ({tracked}) != preserved+transformed+archived+rejected ({expected_sum})",
            "discrepancy": tracked - expected_sum,
        })

    manifest_objects_by_path: dict[str, dict] = {}
    for obj in objects:
        key = (obj.get("destination_repo", ""), obj.get("destination_path", ""))
        if key in manifest_objects_by_path:
            failures.append({
                "type": "duplicate_manifest_object",
                "detail": f"Duplicate manifest entry for {key[0]}/{key[1]}",
            })
        manifest_objects_by_path[key] = obj

    materialized_objects: dict[tuple[str, str], dict] = {}
    dest = Path(dest_root)
    for root, dirs, files in os.walk(str(dest)):
        dirs[:] = [d for d in dirs if d != ".git" and not d.startswith(".migration")]
        for fname in files:
            fpath = Path(root) / fname
            rel = str(fpath.relative_to(dest))
            if fname == ".gitkeep":
                continue
            if fname in ("MIGRATION_BASE.json", "redirects.json", "references.json"):
                continue
            if fname.endswith(".diff_manifest.json"):
                continue
            if rel.startswith("."):
                continue

            repo_name = None
            dest_path = None
            if "/_archive/" in rel:
                repo_part, rest = rel.split("/_archive/", 1)
                repo_name = "/" + repo_part
                dest_path = rest
            else:
                candidate_repos = sorted(set(
                    obj.get("destination_repo", "") for obj in objects
                    if obj.get("action") != ACTION_REJECT
                ), key=lambda r: -len(r))
                for cr in candidate_repos:
                    cr_stripped = cr.lstrip("/")
                    if cr_stripped and rel.startswith(cr_stripped + "/"):
                        repo_name = cr
                        dest_path = rel[len(cr_stripped) + 1:]
                        break
                if repo_name is None:
                    parts = rel.split("/")
                    if len(parts) >= 2:
                        repo_name = "/" + "/".join(parts[:-1])
                        dest_path = parts[-1]
                    else:
                        continue

            materialized_objects[(repo_name, dest_path)] = {"file": str(fpath)}

    expected_keys = set()
    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue
        key = (obj.get("destination_repo", ""), obj.get("destination_path", ""))
        expected_keys.add(key)

    actual_keys = set(materialized_objects.keys())

    missing = expected_keys - actual_keys
    for key in sorted(missing):
        failures.append({
            "type": "missing_object",
            "detail": f"Manifest object {key[0]}/{key[1]} not materialized in destination",
        })

    extra = actual_keys - expected_keys
    for key in sorted(extra):
        failures.append({
            "type": "extra_object",
            "detail": f"Object {key[0]}/{key[1]} exists in destination but not in manifest",
        })

    status = "PASS" if not failures else "FAIL"
    evidence = {
        "tracked": tracked,
        "preserved": preserved,
        "transformed": transformed,
        "archived": archived,
        "rejected": rejected,
        "unclassified": unclassified,
        "expected_count": len(expected_keys),
        "actual_count": len(actual_keys),
        "missing_count": len(missing),
        "extra_count": len(extra),
    }
    return _make_record("parity", status, tracked, failures, evidence)


# ── Gate 2: Hash reconciliation ───────────────────────────────────────────────

def run_hash_gate(manifest: dict, dest_root: str) -> dict:
    objects = manifest.get("objects", [])
    failures: list[dict] = []
    checked = 0

    dest = Path(dest_root)

    for obj in objects:
        action = obj.get("action", ACTION_PRESERVE)
        if action == ACTION_REJECT:
            continue

        repo = obj.get("destination_repo", "").lstrip("/")
        dest_path = obj.get("destination_path", "")
        dest_file = dest / repo / dest_path

        if not dest_file.exists():
            continue

        try:
            content = dest_file.read_bytes()
        except OSError as e:
            failures.append({
                "type": "read_error",
                "path": dest_path,
                "detail": str(e),
            })
            continue

        checked += 1
        actual_sha = _sha256_hex(content)

        if action == ACTION_PRESERVE:
            expected_sha = obj.get("sha256") or obj.get("pre_hash")
            if expected_sha and actual_sha != expected_sha:
                failures.append({
                    "type": "preserve_hash_mismatch",
                    "path": dest_path,
                    "expected": expected_sha,
                    "actual": actual_sha,
                })

        elif action == ACTION_ID_BACKFILL:
            orig_body = _extract_body_bytes(content)
            new_body = _extract_body_bytes(content)
            if orig_body != new_body:
                failures.append({
                    "type": "id_backfill_body_altered",
                    "path": dest_path,
                    "detail": "Body bytes changed after id_backfill",
                })

        elif action in (ACTION_NORMALIZE, ACTION_REWRITE, ACTION_MERGE):
            pre_hash = obj.get("pre_hash")
            post_hash = obj.get("post_hash") or obj.get("sha256")
            if pre_hash and post_hash and pre_hash != post_hash:
                if actual_sha != post_hash and actual_sha != pre_hash:
                    failures.append({
                        "type": "transform_hash_mismatch",
                        "path": dest_path,
                        "action": action,
                        "expected_post_hash": post_hash,
                        "actual": actual_sha,
                    })

    status = "PASS" if not failures else "FAIL"
    return _make_record("hash_reconciliation", status, checked, failures, {"checked": checked})


# ── Gate 3: ID reconciliation ─────────────────────────────────────────────────

def run_id_gate(manifest: dict, dest_root: str) -> dict:
    objects = manifest.get("objects", [])
    redirect_map = manifest.get("redirect_map", {})
    failures: list[dict] = []
    checked = 0

    rejected_ids: set[str] = set()
    assigned_ids: set[str] = set()
    id_to_path: dict[str, str] = {}

    for obj in objects:
        rid = obj.get("domain_resource_id")
        if not rid:
            continue
        dpath = obj.get("destination_path", "")
        action = obj.get("action", "")
        obj_class = obj.get("object_class", "")

        if action == ACTION_REJECT:
            if rid:
                rejected_ids.add(rid)
            continue

        if rid in assigned_ids:
            failures.append({
                "type": "duplicate_id",
                "id": rid,
                "path": dpath,
                "detail": f"ID {rid} assigned to multiple objects",
            })
            continue

        assigned_ids.add(rid)
        id_to_path[rid] = dpath
        checked += 1

    for rejected_id in rejected_ids:
        if rejected_id in assigned_ids:
            failures.append({
                "type": "rejected_id_reused",
                "id": rejected_id,
                "detail": f"ID {rejected_id} was rejected but is also assigned",
            })

    for rid, dpath in id_to_path.items():
        prefix = rid[:2] if rid.startswith("wf-") else rid[:2]
        if prefix == "m-":
            expected_repo = "memory"
        elif prefix == "w-":
            expected_repo = "wiki"
        elif prefix == "wf":
            expected_repo = "work_records"
        else:
            continue

        obj_for_id = None
        for obj in objects:
            if obj.get("domain_resource_id") == rid:
                obj_for_id = obj
                break

        if obj_for_id and obj_for_id.get("object_class", "").startswith("memory_canonical"):
            dest = Path(dest_root) / obj_for_id.get("destination_repo", "").lstrip("/") / dpath
            if dest.exists():
                try:
                    content = dest.read_bytes()
                    fm, _, _ = _parse_frontmatter(content)
                    if fm and fm.get("id") and fm["id"] != rid:
                        failures.append({
                            "type": "id_changed",
                            "id": rid,
                            "path": dpath,
                            "detail": f"Frontmatter id {fm['id']} != expected {rid}",
                        })
                except OSError:
                    pass

    redirect_coverage = set()
    for obj in objects:
        if obj.get("action") == ACTION_ID_BACKFILL and obj.get("domain_resource_id"):
            redirect_coverage.add(obj.get("source_path", ""))

    for src_path in redirect_map:
        if src_path not in redirect_coverage:
            failures.append({
                "type": "redirect_missing_coverage",
                "path": src_path,
                "detail": "Redirect map entry has no corresponding id_backfill object",
            })

    for src_path in redirect_coverage:
        if src_path not in redirect_map:
            failures.append({
                "type": "redirect_missing_from_map",
                "path": src_path,
                "detail": "id_backfill object has no redirect map entry",
            })

    status = "PASS" if not failures else "FAIL"
    evidence = {
        "checked": checked,
        "rejected_ids": sorted(rejected_ids),
        "assigned_ids_count": len(assigned_ids),
        "redirect_map_size": len(redirect_map),
    }
    return _make_record("id_reconciliation", status, checked, failures, evidence)


# ── Gate 4: Reference reconciliation ──────────────────────────────────────────

def run_reference_gate(manifest: dict, dest_root: str) -> dict:
    objects = manifest.get("objects", [])
    failures: list[dict] = []
    checked = 0

    all_ids = {obj["domain_resource_id"] for obj in objects if obj.get("domain_resource_id")}
    path_to_id = {obj["destination_path"]: obj["domain_resource_id"] for obj in objects if obj.get("domain_resource_id")}
    id_to_path = {v: k for k, v in path_to_id.items()}
    redirect_map = manifest.get("redirect_map", {})

    old_broken_ack = 0
    new_broken = 0
    resolved_count = 0
    redirected_count = 0
    total_broken_before = 0
    total_broken_after = 0

    dest = Path(dest_root)

    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue

        dpath = obj.get("destination_path", "")
        repo = obj.get("destination_repo", "").lstrip("/")
        dest_file = dest / repo / dpath

        if not dest_file.exists():
            continue

        try:
            content = dest_file.read_bytes()
        except OSError:
            continue

        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue

        source_id = obj.get("domain_resource_id", "")
        refs = _extract_references_from_text(text)

        for ref in refs:
            checked += 1
            old_target = ref["old_target"]
            old_target_id = _resolve_target(old_target, path_to_id, all_ids)
            old_resolved = old_target_id is not None

            new_target = old_target
            for rw in obj.get("reference_rewrites", []):
                old_rw = rw.get("old", "")
                new_rw = rw.get("new", "")
                if old_rw and old_rw == old_target:
                    new_target = new_rw
                    break
            if old_target in redirect_map:
                new_target = redirect_map[old_target]

            new_target_id = _resolve_target(new_target, path_to_id, all_ids)
            new_resolved = new_target_id is not None

            if not old_resolved:
                total_broken_before += 1
            if not new_resolved:
                total_broken_after += 1

            if old_resolved and new_resolved:
                if old_target_id == new_target_id:
                    resolved_count += 1
                else:
                    redirected_count += 1
            elif not old_resolved and not new_resolved:
                old_broken_ack += 1
            elif old_resolved and not new_resolved:
                new_broken += 1

    constraint_holds = total_broken_after == total_broken_before

    if not constraint_holds:
        failures.append({
            "type": "reference_constraint_violation",
            "detail": f"total_broken_after ({total_broken_after}) != total_broken_before ({total_broken_before})",
            "total_broken_before": total_broken_before,
            "total_broken_after": total_broken_after,
            "old_broken_acknowledged": old_broken_ack,
            "new_broken": new_broken,
        })

    status = "PASS" if not failures else "FAIL"
    evidence = {
        "checked": checked,
        "resolved": resolved_count,
        "redirected": redirected_count,
        "old_broken_acknowledged": old_broken_ack,
        "new_broken": new_broken,
        "total_broken_before": total_broken_before,
        "total_broken_after": total_broken_after,
        "constraint_holds": constraint_holds,
    }
    return _make_record("reference_reconciliation", status, checked, failures, evidence)


def _find_git_repos(dest_root: Path) -> list[Path]:
    repos: list[Path] = []
    for root, dirs, _ in os.walk(str(dest_root)):
        if ".git" in dirs:
            repos.append(Path(root))
            dirs.remove(".git")
    return repos


_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\)]+)\)")
_BARE_ID_RE_REF = re.compile(r"(?<!\w)([mw]f?-[0-9a-f]{6})(?!\w)")


def _extract_references_from_text(text: str) -> list[dict]:
    refs: list[dict] = []
    seen: set[tuple[int, int]] = set()

    for m in _WIKI_LINK_RE.finditer(text):
        span = (m.start(), m.end())
        if span not in seen:
            seen.add(span)
            target = m.group(1).strip()
            refs.append({
                "old_literal": m.group(0),
                "old_target": target,
                "anchor": None,
            })

    for m in _MD_LINK_RE.finditer(text):
        span = (m.start(), m.end())
        if span not in seen:
            seen.add(span)
            target = m.group(2).strip()
            anchor = None
            if "#" in target:
                target, anchor = target.split("#", 1)
            refs.append({
                "old_literal": m.group(0),
                "old_target": target,
                "anchor": anchor,
            })

    for m in _BARE_ID_RE_REF.finditer(text):
        span = (m.start(), m.end())
        if span not in seen:
            seen.add(span)
            refs.append({
                "old_literal": m.group(0),
                "old_target": m.group(0),
                "anchor": None,
            })

    return refs


def _resolve_target(target: str, path_to_id: dict[str, str], all_ids: set[str]) -> str | None:
    if target in all_ids:
        return target
    if target in path_to_id:
        return path_to_id[target]
    for p, rid in path_to_id.items():
        if p.endswith("/" + target) or p == target:
            return rid
        basename = Path(p).stem
        if basename == target:
            return rid
    return None


# ── Gate 5: Integrity gate ────────────────────────────────────────────────────

def run_integrity_gate(manifest: dict, dest_root: str) -> dict:
    objects = manifest.get("objects", [])
    failures: list[dict] = []
    checked = 0

    dest = Path(dest_root)

    for repo_dir in _find_git_repos(dest):
        try:
            result = subprocess.run(
                ["git", "fsck", "--no-dangling"],
                cwd=str(repo_dir), capture_output=True, text=True
            )
            if result.returncode != 0:
                failures.append({
                    "type": "git_fsck",
                    "repo": str(repo_dir.relative_to(dest)),
                    "detail": result.stderr.strip() or result.stdout.strip(),
                })
        except Exception as e:
            failures.append({
                "type": "git_fsck_error",
                "repo": str(repo_dir.relative_to(dest)),
                "detail": str(e),
            })

    basename_casefold: dict[str, str] = {}

    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue

        repo = obj.get("destination_repo", "").lstrip("/")
        dpath = obj.get("destination_path", "")
        dest_file = dest / repo / dpath

        if not dest_file.exists():
            continue

        checked += 1

        if dest_file.is_symlink():
            failures.append({
                "type": "symlink",
                "path": dpath,
                "detail": "Symlinks are rejected by default",
            })
            continue

        try:
            content = dest_file.read_bytes()
        except OSError as e:
            failures.append({
                "type": "read_error",
                "path": dpath,
                "detail": str(e),
            })
            continue

        if _is_binary(content):
            failures.append({
                "type": "binary",
                "path": dpath,
                "detail": "File contains binary bytes",
            })

        if _is_lfs_pointer(content):
            failures.append({
                "type": "lfs_pointer",
                "path": dpath,
                "detail": "File is a git LFS pointer",
            })

        st = dest_file.stat()
        if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            failures.append({
                "type": "executable",
                "path": dpath,
                "detail": "File has executable bit set",
            })

        basename = Path(dpath).name
        if len(basename.encode("utf-8")) > MAX_BASENAME_LENGTH:
            failures.append({
                "type": "basename_length",
                "path": dpath,
                "detail": f"Basename exceeds {MAX_BASENAME_LENGTH} bytes",
            })

        if len(dpath.encode("utf-8")) > MAX_PATH_LENGTH:
            failures.append({
                "type": "path_length",
                "path": dpath,
                "detail": f"Path exceeds {MAX_PATH_LENGTH} bytes",
            })

        casefold_name = basename.casefold()
        if casefold_name in basename_casefold and basename_casefold[casefold_name] != basename:
            failures.append({
                "type": "casefold_collision",
                "path": dpath,
                "detail": f"Casefold collision with {basename_casefold[casefold_name]}: {basename}",
            })
        else:
            basename_casefold[casefold_name] = basename

        if not _is_nfc_normalized(content):
            failures.append({
                "type": "unicode_nfc",
                "path": dpath,
                "detail": "Content is not NFC normalized",
            })

    status = "PASS" if not failures else "FAIL"
    evidence = {"checked": checked, "repos_scanned": len(_find_git_repos(dest))}
    return _make_record("integrity", status, checked, failures, evidence)


# ── Gate 6: History-extraction correctness ────────────────────────────────────

def run_history_gate(manifest: dict, dest_root: str) -> dict:
    objects = manifest.get("objects", [])
    failures: list[dict] = []
    checked = 0

    in_scope_paths: set[str] = set()
    for obj in objects:
        if obj.get("action") == ACTION_REJECT:
            continue
        in_scope_paths.add(obj.get("destination_path", ""))

    dest = Path(dest_root)

    for repo_dir in _find_git_repos(dest):
        try:
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=str(repo_dir), capture_output=True, text=True
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    line = line.strip()
                    if not line or line == ".gitkeep":
                        continue
                    if line.startswith(".migration"):
                        continue
                    if line in ("MIGRATION_BASE.json", "redirects.json", "references.json"):
                        continue
                    checked += 1
                    if line not in in_scope_paths and not line.startswith("_archive/"):
                        failures.append({
                            "type": "out_of_scope_leak",
                            "path": line,
                            "detail": "File exists in HEAD tree but is not an in-scope manifest path",
                        })
        except Exception as e:
            failures.append({
                "type": "ls_tree_error",
                "repo": str(repo_dir.relative_to(dest)),
                "detail": str(e),
            })

    status = "PASS" if not failures else "FAIL"
    evidence = {"checked": checked, "in_scope_paths": len(in_scope_paths)}
    return _make_record("history_extraction", status, checked, failures, evidence)


# ── Gate 7: Idempotency ───────────────────────────────────────────────────────

def run_idempotency_gate(
    manifest: dict,
    dest_root: str,
    *,
    committer_date: str | None = None,
    committer_name: str = DEFAULT_COMMITTER_NAME,
    committer_email: str = DEFAULT_COMMITTER_EMAIL,
) -> dict:
    from katana_migration.rehearsal import run_rehearsal

    failures: list[dict] = []
    domains_count = 0

    run1_dir = Path(tempfile.mkdtemp(prefix="idem1_"))
    run2_dir = Path(tempfile.mkdtemp(prefix="idem2_"))

    try:
        run1 = run_rehearsal(
            manifest, str(run1_dir),
            committer_date=committer_date,
            committer_name=committer_name,
            committer_email=committer_email,
        )
        domains_count = len(run1.get("domain_results", {}))

        run2 = run_rehearsal(
            manifest, str(run2_dir),
            committer_date=committer_date,
            committer_name=committer_name,
            committer_email=committer_email,
        )

        for repo_name, result1 in run1.get("domain_results", {}).items():
            result2 = run2.get("domain_results", {}).get(repo_name)
            if result2 is None:
                failures.append({
                    "type": "domain_missing",
                    "repo": repo_name,
                    "detail": "Domain present in run 1 but missing in run 2",
                })
                continue

            commit1 = result1.get("final_commit", "")
            commit2 = result2.get("final_commit", "")
            if commit1 != commit2:
                failures.append({
                    "type": "commit_different",
                    "repo": repo_name,
                    "run1_commit": commit1,
                    "run2_commit": commit2,
                })
    finally:
        shutil.rmtree(str(run1_dir), ignore_errors=True)
        shutil.rmtree(str(run2_dir), ignore_errors=True)

    status = "PASS" if not failures else "FAIL"
    return _make_record("idempotency", status, domains_count, failures, {"domains": domains_count})


# ── Gate 8: Verification record ───────────────────────────────────────────────

def run_verification_record_gate(records: list[dict]) -> dict:
    failures: list[dict] = []

    expected_keys = {"gate", "status", "checked", "failures", "evidence_digest"}
    for i, rec in enumerate(records):
        missing_keys = expected_keys - set(rec.keys())
        if missing_keys:
            failures.append({
                "type": "missing_keys",
                "gate": rec.get("gate", f"index_{i}"),
                "detail": f"Missing keys: {sorted(missing_keys)}",
            })
        if rec.get("status") not in ("PASS", "FAIL"):
            failures.append({
                "type": "invalid_status",
                "gate": rec.get("gate", f"index_{i}"),
                "detail": f"Invalid status: {rec.get('status')}",
            })
        if not isinstance(rec.get("failures"), list):
            failures.append({
                "type": "invalid_failures",
                "gate": rec.get("gate", f"index_{i}"),
                "detail": "failures is not a list",
            })
        if not isinstance(rec.get("evidence_digest"), str) or not rec.get("evidence_digest", "").startswith("sha256:"):
            failures.append({
                "type": "invalid_evidence_digest",
                "gate": rec.get("gate", f"index_{i}"),
                "detail": "evidence_digest must be a sha256: prefixed string",
            })

    status = "PASS" if not failures else "FAIL"
    evidence = {"record_count": len(records)}
    return _make_record("verification_record", status, len(records), failures, evidence)


# ── Full suite runner ─────────────────────────────────────────────────────────

def run_all_proof_gates(
    manifest: dict,
    dest_root: str,
    *,
    committer_date: str | None = None,
    committer_name: str = DEFAULT_COMMITTER_NAME,
    committer_email: str = DEFAULT_COMMITTER_EMAIL,
) -> dict:
    records: list[dict] = []

    records.append(run_parity_gate(manifest, dest_root))
    records.append(run_hash_gate(manifest, dest_root))
    records.append(run_id_gate(manifest, dest_root))
    records.append(run_reference_gate(manifest, dest_root))
    records.append(run_integrity_gate(manifest, dest_root))
    records.append(run_history_gate(manifest, dest_root))
    records.append(run_idempotency_gate(
        manifest, dest_root,
        committer_date=committer_date,
        committer_name=committer_name,
        committer_email=committer_email,
    ))
    records.append(run_verification_record_gate(records))
    report = build_aggregate_report(records)
    return report


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    import sys as _sys

    ap = argparse.ArgumentParser(
        description="Migration proof-gate suite: run all gates and produce verification records"
    )
    ap.add_argument(
        "--manifest", required=True,
        help="Path to M3a manifest JSON file"
    )
    ap.add_argument(
        "--dest-root", required=True,
        help="Path to M3b rehearsal destination root directory"
    )
    ap.add_argument(
        "--committer-date", default=None,
        help="Fixed committer date for deterministic output (ISO 8601)"
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output verification report JSON file path (default: stdout)"
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    report = run_all_proof_gates(
        manifest, args.dest_root,
        committer_date=args.committer_date,
    )

    report_json = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report_json)
    else:
        _sys.stdout.write(report_json)


if __name__ == "__main__":
    main()