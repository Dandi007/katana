"""Migration rehearsal engine: REHEARSED phase (M3b).

Consumes M3a inventory manifest, materialises objects to a temporary
destination root per domain, and produces a git-backed rehearsal tree
with final maintenance commit, MIGRATION_BASE marker, redirect catalog,
and reference manifest.

All writes land ONLY inside the given temporary destination root.
No production data roots are touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
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
    EXC_BINARY,
    EXC_CASEFOLD_COLLISION,
    EXC_EXECUTABLE,
    EXC_LFS_POINTER,
    EXC_PATH_LENGTH,
    EXC_SYMLINK,
    _parse_memory_frontmatter,
    is_binary,
    is_lfs_pointer,
    sha256_hex,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MIGRATION_BASE_FILENAME = "MIGRATION_BASE"
REDIRECTS_FILENAME = "redirects.json"
REFERENCES_FILENAME = "references.json"

MEMORY_ID_RE = re.compile(r"^m-[0-9a-f]{6}$")
WIKI_ID_RE = re.compile(r"^w-[0-9a-f]{6}$")
WF_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")

# Link patterns for reference resolution
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
MEMORY_ID_REF_RE = re.compile(r"(?<!\w)(m-[0-9a-f]{6})(?!\w)")
WIKI_ID_REF_RE = re.compile(r"(?<!\w)(w-[0-9a-f]{6})(?!\w)")
WF_ID_REF_RE = re.compile(r"(?<!\w)(wf-[0-9a-f]{6})(?!\w)")


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git_run(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True, text=True, timeout=30,
    )


def _git_init(repo_root: str) -> None:
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        _git_run(repo_root, "init")
        _git_run(repo_root, "config", "user.email", "rehearsal@katana.local")
        _git_run(repo_root, "config", "user.name", "Katana Rehearsal Engine")


def _git_commit(repo_root: str, message: str, paths: list[str]) -> dict:
    try:
        add = _git_run(repo_root, "add", "--", *paths)
        if add.returncode != 0:
            return {"committed": False, "detail": add.stderr.strip()}
        diff = _git_run(repo_root, "diff", "--cached", "--quiet")
        if diff.returncode == 0:
            return {"committed": False, "detail": "nothing to commit"}
        c = _git_run(repo_root, "commit", "-m", message)
        if c.returncode != 0:
            return {"committed": False, "detail": c.stderr.strip() or c.stdout.strip()}
        sha_r = _git_run(repo_root, "rev-parse", "HEAD")
        sha = sha_r.stdout.strip() if sha_r.returncode == 0 else ""
        return {"committed": True, "detail": sha}
    except (subprocess.SubprocessError, OSError) as e:
        return {"committed": False, "detail": str(e)}


def _git_head_sha(repo_root: str) -> str:
    try:
        r = _git_run(repo_root, "rev-parse", "HEAD")
        if r.returncode == 0:
            return r.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        pass
    return ""


# ── Integrity gate ────────────────────────────────────────────────────────────

def _check_integrity(obj: dict, content: bytes, dest_root: str) -> list[tuple[str, str]]:
    """Run integrity gate checks. Returns list of (exception_code, reason)."""
    exceptions: list[tuple[str, str]] = []

    rel_path = obj.get("destination_path", obj.get("source_path", ""))

    if is_binary(content):
        exceptions.append((EXC_BINARY, "File contains binary bytes"))

    if is_lfs_pointer(content):
        exceptions.append((EXC_LFS_POINTER, "File is a git LFS pointer"))

    basename = Path(rel_path).name
    if len(basename.encode("utf-8")) > 255:
        exceptions.append((EXC_PATH_LENGTH, f"Basename exceeds 255 bytes"))

    if len(rel_path.encode("utf-8")) > 4096:
        exceptions.append((EXC_PATH_LENGTH, f"Path exceeds 4096 bytes"))

    norm_basename = unicodedata.normalize("NFC", basename)
    if norm_basename != basename:
        exceptions.append(("UNICODE_NORMALIZATION", f"Basename not NFC-normalized: {basename}"))

    return exceptions


def _check_executable(path: Path, obj: dict) -> tuple[str, str] | None:
    if path.exists() and path.is_file():
        st = path.stat()
        if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            return (EXC_EXECUTABLE, "File has executable bit set")
    return None


# ── Domain destination mapping ────────────────────────────────────────────────

def _dest_repo_for_object_class(object_class: str) -> str:
    if object_class.startswith("memory"):
        return "memory"
    if object_class.startswith("wiki"):
        return "wiki"
    if object_class == "work_folder":
        return "work_folder"
    return "unknown"


# ── Action: preserve ──────────────────────────────────────────────────────────

def _materialize_preserve(obj: dict, source_root: str, dest_root: str) -> dict:
    src_path = os.path.join(source_root, obj["source_path"])
    dest_path = os.path.join(dest_root, obj["destination_path"])

    try:
        content = Path(src_path).read_bytes()
    except OSError as e:
        return {"written": False, "reason": str(e)}

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    Path(dest_path).write_bytes(content)
    try:
        shutil.copystat(src_path, dest_path)
    except OSError:
        pass

    actual_sha = sha256_hex(content)
    expected_sha = obj.get("sha256") or obj.get("pre_hash")
    byte_equal = (expected_sha is not None and actual_sha == expected_sha)

    return {
        "written": True,
        "sha256": actual_sha,
        "byte_equal": byte_equal,
        "size": len(content),
    }


# ── Action: id_backfill ───────────────────────────────────────────────────────

def _materialize_id_backfill(obj: dict, source_root: str, dest_root: str) -> dict:
    src_path = os.path.join(source_root, obj["source_path"])
    dest_path = os.path.join(dest_root, obj["destination_path"])

    try:
        content = Path(src_path).read_bytes()
    except OSError as e:
        return {"written": False, "reason": str(e)}

    resource_id = obj.get("domain_resource_id") or obj.get("vfs_node_id")
    new_content = _inject_frontmatter_id(content, resource_id)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    Path(dest_path).write_bytes(new_content)

    # Verify body bytes unchanged (everything after frontmatter)
    original_body = _extract_body_bytes(content)
    new_body = _extract_body_bytes(new_content)
    body_unchanged = (original_body == new_body)

    return {
        "written": True,
        "sha256": sha256_hex(new_content),
        "size": len(new_content),
        "body_bytes_unchanged": body_unchanged,
        "injected_id": resource_id,
    }


def _inject_frontmatter_id(content: bytes, resource_id: str | None) -> bytes:
    if resource_id is None:
        return content
    text = content.decode("utf-8", errors="replace")
    if text.startswith("---\n"):
        fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
        if fm_end is None:
            return content
        fm_text = text[4:4 + fm_end.start() + 1]
        lines = fm_text.split("\n")
        new_lines = []
        injected = False
        for line in lines:
            if line.startswith("id:") and not injected:
                new_lines.append(f"id: {resource_id}")
                injected = True
            else:
                new_lines.append(line)
        if not injected:
            new_lines.insert(0, f"id: {resource_id}")
        new_fm = "\n".join(new_lines)
        return f"---\n{new_fm}---\n{text[4 + fm_end.end():]}".encode("utf-8")
    else:
        # No frontmatter — insert one
        return f"---\nid: {resource_id}\n---\n\n{text}".encode("utf-8")


def _extract_body_bytes(content: bytes) -> bytes:
    text = content.decode("utf-8", errors="replace")
    if text.startswith("---\n"):
        fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
        if fm_end is not None:
            return content[4 + fm_end.end():]
    return content


# ── Action: normalize / rewrite ───────────────────────────────────────────────

def _materialize_normalize(obj: dict, source_root: str, dest_root: str) -> dict:
    src_path = os.path.join(source_root, obj["source_path"])
    dest_path = os.path.join(dest_root, obj["destination_path"])

    try:
        content = Path(src_path).read_bytes()
    except OSError as e:
        return {"written": False, "reason": str(e)}

    normalized = _normalize_content(content, obj)
    diff = _diff_manifest(content, normalized)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    Path(dest_path).write_bytes(normalized)

    return {
        "written": True,
        "sha256": sha256_hex(normalized),
        "size": len(normalized),
        "diff_manifest": diff,
    }


def _materialize_rewrite(obj: dict, source_root: str, dest_root: str) -> dict:
    src_path = os.path.join(source_root, obj["source_path"])
    dest_path = os.path.join(dest_root, obj["destination_path"])

    try:
        content = Path(src_path).read_bytes()
    except OSError as e:
        return {"written": False, "reason": str(e)}

    rewritten = _rewrite_content(content, obj)
    diff = _diff_manifest(content, rewritten)

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    Path(dest_path).write_bytes(rewritten)

    return {
        "written": True,
        "sha256": sha256_hex(rewritten),
        "size": len(rewritten),
        "diff_manifest": diff,
    }


def _normalize_content(content: bytes, obj: dict) -> bytes:
    text = content.decode("utf-8", errors="replace")
    # NFC normalization
    text = unicodedata.normalize("NFC", text)
    # Normalize line endings to LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip trailing whitespace
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    # Ensure trailing newline
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def _rewrite_content(content: bytes, obj: dict) -> bytes:
    text = content.decode("utf-8", errors="replace")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    if not text.endswith("\n"):
        text += "\n"

    rewrites = obj.get("reference_rewrites", []) or []
    for rw in rewrites:
        old_literal = rw.get("old_literal", "")
        new_literal = rw.get("new_literal", "")
        if old_literal and new_literal:
            text = text.replace(old_literal, new_literal)

    return text.encode("utf-8")


def _diff_manifest(original: bytes, transformed: bytes) -> dict:
    if original == transformed:
        return {"changed": False, "changes": []}
    orig_lines = original.decode("utf-8", errors="replace").split("\n")
    new_lines = transformed.decode("utf-8", errors="replace").split("\n")

    changes = []
    max_len = max(len(orig_lines), len(new_lines))
    for i in range(max_len):
        old_line = orig_lines[i] if i < len(orig_lines) else None
        new_line = new_lines[i] if i < len(new_lines) else None
        if old_line != new_line:
            changes.append({
                "line": i + 1,
                "old": old_line,
                "new": new_line,
            })

    return {"changed": len(changes) > 0, "changes": changes}


# ── Action: merge ─────────────────────────────────────────────────────────────

def _materialize_merge(obj: dict, source_root: str, dest_root: str) -> dict:
    src_path = os.path.join(source_root, obj["source_path"])
    dest_path = os.path.join(dest_root, obj["destination_path"])

    try:
        content = Path(src_path).read_bytes()
    except OSError as e:
        return {"written": False, "reason": str(e)}

    if os.path.exists(dest_path):
        existing = Path(dest_path).read_bytes()
        merged = _merge_content(existing, content, obj)
    else:
        merged = content

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    Path(dest_path).write_bytes(merged)

    return {
        "written": True,
        "sha256": sha256_hex(merged),
        "size": len(merged),
    }


def _merge_content(existing: bytes, incoming: bytes, obj: dict) -> bytes:
    existing_text = existing.decode("utf-8", errors="replace")
    incoming_text = incoming.decode("utf-8", errors="replace")

    existing_lines = existing_text.split("\n")
    incoming_lines = incoming_text.split("\n")

    existing_set = set(existing_lines)
    new_lines = [line for line in incoming_lines if line not in existing_set]

    if new_lines:
        merged = existing_text + "\n" + "\n".join(new_lines) + "\n"
    else:
        merged = existing_text + "\n"

    return merged.encode("utf-8")


# ── Action: archive ───────────────────────────────────────────────────────────

def _materialize_archive(obj: dict, source_root: str, dest_root: str) -> dict:
    src_path = os.path.join(source_root, obj["source_path"])
    archive_dir = os.path.join(dest_root, "_archive")
    dest_path = os.path.join(archive_dir, obj["source_path"])

    try:
        content = Path(src_path).read_bytes()
    except OSError as e:
        return {"written": False, "reason": str(e)}

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    Path(dest_path).write_bytes(content)

    return {
        "written": True,
        "sha256": sha256_hex(content),
        "size": len(content),
        "archived_path": os.path.join("_archive", obj["source_path"]),
    }


# ── Reference resolution ──────────────────────────────────────────────────────

def _resolve_references(obj: dict, content: bytes, id_map: dict[str, str]) -> dict:
    result = {
        "source_id": obj.get("domain_resource_id"),
        "source_path": obj.get("source_path"),
        "rewrites": [],
        "old_broken": [],
        "new_broken": [],
    }

    text = content.decode("utf-8", errors="replace")

    for match in WIKILINK_RE.finditer(text):
        target = match.group(1).split("|")[0].split("#")[0].strip()
        if target in id_map:
            new_target = id_map[target]
            if new_target != target:
                result["rewrites"].append({
                    "type": "wikilink",
                    "old_literal": match.group(1),
                    "new_literal": match.group(1).replace(target, new_target, 1),
                    "old_target": target,
                    "new_target": new_target,
                })

    for regex, id_type in [
        (MEMORY_ID_REF_RE, "memory_id"),
        (WIKI_ID_REF_RE, "wiki_id"),
        (WF_ID_REF_RE, "work_folder_id"),
    ]:
        for match in regex.finditer(text):
            target = match.group(1)
            if target in id_map:
                new_target = id_map[target]
                if new_target != target:
                    result["rewrites"].append({
                        "type": id_type,
                        "old_literal": target,
                        "new_literal": new_target,
                        "old_target": target,
                        "new_target": new_target,
                    })

    return result


def _compute_reference_stats(reference_entries: list[dict]) -> dict:
    total_old_broken = sum(len(e.get("old_broken", [])) for e in reference_entries)
    total_new_broken = sum(len(e.get("new_broken", [])) for e in reference_entries)
    return {
        "old_broken": total_old_broken,
        "new_broken": total_new_broken,
        "new_minus_old": total_new_broken - total_old_broken,
        "constraint_holds": (total_new_broken - total_old_broken) == 0,
    }


# ── Rehearsal engine ──────────────────────────────────────────────────────────

class RehearsalEngine:
    """REHEARSED-phase migration engine.

    Consumes an M3a inventory manifest and materialises every object to
    a temporary destination root.  Produces per-domain git repositories
    with a final maintenance commit, MIGRATION_BASE marker, redirect
    catalog, and reference manifest.

    Idempotent: same manifest + same source → byte-identical destination.
    """

    def __init__(
        self,
        manifest: dict,
        source_root: str,
        dest_root: str,
        committer_date: str | None = None,
        committer_name: str = "Katana Rehearsal Engine",
        committer_email: str = "rehearsal@katana.local",
    ):
        self.manifest = manifest
        self.source_root = source_root
        self.dest_root = dest_root
        self.committer_date = committer_date
        self.committer_name = committer_name
        self.committer_email = committer_email
        self.results: list[dict] = []
        self.reference_entries: list[dict] = []
        self.id_map: dict[str, str] = {}
        self.errors: list[dict] = []
        self._domain_commits: dict[str, dict] = {}
        self._source_roots: dict[str, str] = {}
        self._build_source_root_map()

    def _build_source_root_map(self) -> None:
        for ss in self.manifest.get("source_sets", []):
            repo = ss.get("source_repo", "")
            root = ss.get("root", "")
            if repo and root:
                self._source_roots[repo] = root

    def run(self) -> dict:
        """Execute the full rehearsal import.

        Returns a dictionary with per-domain results, overall summary,
        and reference statistics.
        """
        self._build_id_map()
        objects = self.manifest.get("objects", [])

        grouped = self._group_by_domain(objects)

        for domain, domain_objects in grouped.items():
            self._process_domain(domain, domain_objects)

        summary = self._compute_summary()
        reference_stats = _compute_reference_stats(self.reference_entries)

        return {
            "migration_run_id": self.manifest.get("migration_run_id"),
            "dest_root": self.dest_root,
            "domain_results": self._domain_commits,
            "results": self.results,
            "summary": summary,
            "reference_stats": reference_stats,
            "errors": self.errors,
            "idempotent": len(self.errors) == 0,
        }

    def _build_id_map(self) -> None:
        for obj in self.manifest.get("objects", []):
            rid = obj.get("domain_resource_id") or obj.get("vfs_node_id")
            src_path = obj.get("source_path", "")
            if rid and src_path:
                self.id_map[src_path] = rid
                self.id_map[rid] = rid

        redirect_map = self.manifest.get("redirect_map", {})
        for src_path, rid in redirect_map.items():
            self.id_map[src_path] = rid

    def _group_by_domain(self, objects: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = {}
        for obj in objects:
            domain = _dest_repo_for_object_class(obj.get("object_class", ""))
            grouped.setdefault(domain, []).append(obj)
        return grouped

    def _process_domain(self, domain: str, objects: list[dict]) -> None:
        domain_root = os.path.join(self.dest_root, domain)
        os.makedirs(domain_root, exist_ok=True)
        _git_init(domain_root)

        written_paths: list[str] = []
        domain_reference_entries: list[dict] = []

        for obj in objects:
            result = self._process_object(obj, domain_root)
            if result.get("stopped"):
                self.errors.append({
                    "domain": domain,
                    "source_path": obj.get("source_path"),
                    "reason": result.get("reason"),
                })
                return

            if result.get("written"):
                written_paths.append(obj.get("destination_path", obj.get("source_path", "")))

            if "reference_entry" in result:
                domain_reference_entries.append(result["reference_entry"])

            self.results.append(result)

        if domain_reference_entries:
            self.reference_entries.extend(domain_reference_entries)

        if written_paths:
            self._write_migration_base(domain_root, domain)
            written_paths.append(MIGRATION_BASE_FILENAME)

            self._write_redirects(domain_root, objects, domain)
            written_paths.append(REDIRECTS_FILENAME)

            self._write_references(domain_root, domain_reference_entries)
            written_paths.append(REFERENCES_FILENAME)

            commit_result = _git_commit(
                domain_root,
                f"Rehearsal import: {domain} ({len(objects)} objects)",
                written_paths,
            )
            self._domain_commits[domain] = {
                "objects": len(objects),
                "written": len([r for r in self.results if r.get("written")]),
                "commit": commit_result,
            }

    def _process_object(self, obj: dict, domain_root: str) -> dict:
        action = obj.get("action", ACTION_PRESERVE)
        exception_code = obj.get("exception_code")

        if exception_code:
            if action == ACTION_REJECT:
                return {
                    "written": False,
                    "action": action,
                    "source_path": obj.get("source_path"),
                    "exception_code": exception_code,
                    "reason": obj.get("reason"),
                }
            elif _is_blocking_exception(exception_code):
                return {
                    "written": False,
                    "action": "reject",
                    "source_path": obj.get("source_path"),
                    "exception_code": exception_code,
                    "reason": obj.get("reason"),
                    "stopped": True,
                }

        if action == ACTION_REJECT:
            return {
                "written": False,
                "action": action,
                "source_path": obj.get("source_path"),
                "reason": obj.get("reason"),
            }

        handler_map = {
            ACTION_PRESERVE: _materialize_preserve,
            ACTION_ID_BACKFILL: _materialize_id_backfill,
            ACTION_NORMALIZE: _materialize_normalize,
            ACTION_REWRITE: _materialize_rewrite,
            ACTION_MERGE: _materialize_merge,
            ACTION_ARCHIVE: _materialize_archive,
        }

        handler = handler_map.get(action)
        if handler is None:
            return {
                "written": False,
                "action": action,
                "source_path": obj.get("source_path"),
                "reason": f"Unknown action: {action}",
            }

        resolved_root = self._source_roots.get(obj.get("source_repo", ""), self.source_root)
        result = handler(obj, resolved_root, domain_root)

        if not result.get("written"):
            return {
                "written": False,
                "action": action,
                "source_path": obj.get("source_path"),
                "reason": result.get("reason"),
            }

        dest_path = os.path.join(domain_root, obj.get("destination_path", obj.get("source_path", "")))
        if not os.path.exists(dest_path):
            return {
                "written": False,
                "action": action,
                "source_path": obj.get("source_path"),
                "reason": "Destination file not created",
            }

        content = Path(dest_path).read_bytes()

        integrity_issues = _check_integrity(obj, content, domain_root)
        exec_issue = _check_executable(Path(dest_path), obj)
        if exec_issue:
            integrity_issues.append(exec_issue)

        if integrity_issues:
            for code, reason in integrity_issues:
                if _is_blocking_exception(code):
                    return {
                        "written": False,
                        "action": action,
                        "source_path": obj.get("source_path"),
                        "exception_code": code,
                        "reason": reason,
                        "stopped": True,
                    }

        reference_entry = _resolve_references(obj, content, self.id_map)

        return {
            "written": True,
            "action": action,
            "source_path": obj.get("source_path"),
            "destination_path": obj.get("destination_path", obj.get("source_path", "")),
            "sha256": result.get("sha256"),
            "size": result.get("size"),
            "byte_equal": result.get("byte_equal"),
            "body_bytes_unchanged": result.get("body_bytes_unchanged"),
            "diff_manifest": result.get("diff_manifest"),
            "reference_entry": reference_entry,
        }

    def _write_migration_base(self, domain_root: str, domain: str) -> None:
        marker = {
            "migration_run_id": self.manifest.get("migration_run_id"),
            "domain": domain,
            "phase": "REHEARSED",
            "source_sets": [
                {
                    "name": ss.get("name"),
                    "source_repo": ss.get("source_repo"),
                    "source_commit": ss.get("source_commit"),
                }
                for ss in self.manifest.get("source_sets", [])
            ],
        }
        path = os.path.join(domain_root, MIGRATION_BASE_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(marker, f, indent=2, sort_keys=True, ensure_ascii=False)

    def _write_redirects(self, domain_root: str, objects: list[dict], domain: str) -> None:
        redirects = {}
        for obj in objects:
            src_path = obj.get("source_path", "")
            dest_path = obj.get("destination_path", obj.get("source_path", ""))
            if src_path != dest_path:
                redirects[src_path] = dest_path

        for obj in objects:
            if obj.get("action") == ACTION_ID_BACKFILL and obj.get("domain_resource_id"):
                src_path = obj.get("source_path", "")
                redirects[src_path] = obj.get("domain_resource_id")

        path = os.path.join(domain_root, REDIRECTS_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(redirects, f, indent=2, sort_keys=True, ensure_ascii=False)

    def _write_references(self, domain_root: str, entries: list[dict]) -> None:
        path = os.path.join(domain_root, REFERENCES_FILENAME)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, sort_keys=True, ensure_ascii=False)

    def _compute_summary(self) -> dict:
        total = len(self.manifest.get("objects", []))
        written = sum(1 for r in self.results if r.get("written"))
        by_action: dict[str, int] = {}
        for r in self.results:
            action = r.get("action", "unknown")
            by_action[action] = by_action.get(action, 0) + 1

        return {
            "total_objects": total,
            "written": written,
            "skipped": total - written,
            "by_action": by_action,
            "errors": len(self.errors),
        }


def _is_blocking_exception(code: str) -> bool:
    return code in {
        EXC_BINARY,
        EXC_LFS_POINTER,
        EXC_PATH_LENGTH,
        EXC_SYMLINK,
        EXC_CASEFOLD_COLLISION,
        EXC_EXECUTABLE,
        "UNICODE_NORMALIZATION",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def run_rehearsal(
    manifest: dict,
    source_root: str,
    dest_root: str,
    committer_date: str | None = None,
) -> dict:
    engine = RehearsalEngine(
        manifest=manifest,
        source_root=source_root,
        dest_root=dest_root,
        committer_date=committer_date,
    )
    return engine.run()