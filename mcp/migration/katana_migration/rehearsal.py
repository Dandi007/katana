"""Migration rehearsal engine: REHEARSED-phase full import engine.

M3b REHEARSED-phase engine.  Consumes M3a manifest, materializes every object
per its declared action into a frozen destination git tree, and produces a
final maintenance commit with MIGRATION_BASE marker, redirect catalog, and
reference manifest.  Idempotent: same manifest + same source → byte-identical
destination (stable commit tree when committer_date is injected).
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

import yaml

# ── Re-exported constants (mirrors inventory.py) ──────────────────────────────

ACTION_PRESERVE = "preserve"
ACTION_ID_BACKFILL = "id_backfill"
ACTION_NORMALIZE = "normalize"
ACTION_REWRITE = "rewrite"
ACTION_MERGE = "merge"
ACTION_ARCHIVE = "archive"
ACTION_QUARANTINE = "quarantine"
ACTION_REJECT = "reject"

_TRANSFORM_ACTIONS = {
    ACTION_ID_BACKFILL,
    ACTION_NORMALIZE,
    ACTION_REWRITE,
    ACTION_MERGE,
    ACTION_QUARANTINE,
}

NORMALIZE_EXECUTABLE = "clear_executable_bit"
NORMALIZE_UNICODE_NFC = "unicode_nfc"

# Integrity gate codes
GATE_SYMLINK = "SYMLINK"
GATE_BINARY = "BINARY"
GATE_LFS = "LFS_POINTER"
GATE_PATH_LENGTH = "PATH_LENGTH"
GATE_CASEFOLD = "CASEFOLD_COLLISION"
GATE_EXECUTABLE = "EXECUTABLE"
GATE_UNICODE_NFC = "UNICODE_NFC"

# Reference disposition codes
DISPOSITION_RESOLVED = "resolved"
DISPOSITION_REDIRECTED = "redirected"
DISPOSITION_BROKEN_OLD_ACK = "broken_old_ack"
DISPOSITION_BROKEN_NEW = "broken_new"

# Path length limits
MAX_BASENAME_LENGTH = 255
MAX_PATH_LENGTH = 4096

# Memoization cache for git blob OID computation
_BLOB_OID_CACHE: dict[bytes, str] = {}

# ID literal patterns
_MEMORY_ID_RE = re.compile(r"^m-[0-9a-f]{6}$")
_WIKI_ID_RE = re.compile(r"^w-[0-9a-f]{6}$")
_WF_ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")
_PATH_PRESERVING_CLASSES = {"work_folder", "wiki_raw"}

# Reference extraction patterns
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]+)?\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_BARE_ID_RE = re.compile(r"(?<!\w)([mw]f?-[0-9a-f]{6})(?!\w)")
_FRONTMATTER_END_RE = re.compile(rb"\n---[ \t]*(?=\n|$)")
_FRONTMATTER_ID_RE = re.compile(rb"(?m)^id[ \t]*:")

# Git commit date format (ISO 8601, used by git)
_GIT_DATE_FMT = "%Y-%m-%dT%H:%M:%S%z"

# Default committer info
_DEFAULT_COMMITTER_NAME = "Migration Engine"
_DEFAULT_COMMITTER_EMAIL = "migration@katana.local"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_id_literal(s: str) -> bool:
    return bool(_MEMORY_ID_RE.match(s) or _WIKI_ID_RE.match(s) or _WF_ID_RE.match(s))


def _git_blob_oid(content: bytes) -> str:
    if content in _BLOB_OID_CACHE:
        return _BLOB_OID_CACHE[content]
    header = f"blob {len(content)}\0".encode()
    oid = hashlib.sha1(header + content).hexdigest()
    _BLOB_OID_CACHE[content] = oid
    return oid


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
        return True
    return unicodedata.is_normalized("NFC", text)


def _extract_body_bytes(content: bytes) -> bytes:
    if not content.startswith(b"---\n"):
        return content
    fm_end = _FRONTMATTER_END_RE.search(content, 4)
    if fm_end is None:
        return content
    body_start = fm_end.end()
    if content[body_start:body_start + 1] == b"\n":
        body_start += 1
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


def _write_frontmatter(fm: dict, body: bytes) -> bytes:
    lines = ["---"]
    for k, v in fm.items():
        lines.append(f"{k}: {_yaml_value(v)}")
    lines.append("---")
    header = "\n".join(lines).encode("utf-8")
    return header + body


def _yaml_value(v: object) -> str:
    if isinstance(v, str):
        if any(ch in v for ch in ("{", "}", "[", "]", ":", "#", "&", "*", "!", ">", "|", "'", '"', "%", "@", "`", ",")):
            return yaml.dump(v, default_flow_style=False, allow_unicode=True).rstrip("\n")
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return yaml.dump(v, default_flow_style=False, allow_unicode=True).rstrip("\n")
    return str(v)


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
                "link_type": "wiki",
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
                "link_type": "markdown",
            })

    for m in _BARE_ID_RE.finditer(text):
        span = (m.start(), m.end())
        if span not in seen:
            seen.add(span)
            refs.append({
                "old_literal": m.group(0),
                "old_target": m.group(0),
                "anchor": None,
                "link_type": "bare_id",
            })

    return refs


def _canonical_frontmatter_keys(object_class: str) -> list[str]:
    if object_class.startswith("memory"):
        return ["id", "name", "description", "status", "last_verified", "tags", "related"]
    if object_class.startswith("wiki"):
        return ["id", "title", "tags", "status", "last_verified", "source"]
    if object_class == "work_folder":
        return ["id", "task", "status", "priority", "deadline", "tags"]
    return []


def _required_frontmatter_defaults(object_class: str) -> dict[str, object]:
    if object_class.startswith("memory"):
        return {"status": "active", "last_verified": "2026-01-01"}
    if object_class.startswith("wiki"):
        return {"status": "draft", "last_verified": "2026-01-01"}
    if object_class == "work_folder":
        return {"status": "pending"}
    return {}


# ── Rehearsal Engine ──────────────────────────────────────────────────────────

class RehearsalError(Exception):
    pass


class RehearsalEngine:
    def __init__(
        self,
        manifest: dict,
        dest_root: str,
        *,
        committer_date: str | None = None,
        committer_name: str = _DEFAULT_COMMITTER_NAME,
        committer_email: str = _DEFAULT_COMMITTER_EMAIL,
    ):
        self.manifest = manifest
        self.dest_root = Path(dest_root)
        self.committer_date = committer_date or "2026-01-01T00:00:00+0000"
        self.committer_name = committer_name
        self.committer_email = committer_email
        self._git_env = {
            **os.environ,
            "GIT_COMMITTER_DATE": self.committer_date,
            "GIT_AUTHOR_DATE": self.committer_date,
            "GIT_COMMITTER_NAME": self.committer_name,
            "GIT_COMMITTER_EMAIL": self.committer_email,
            "GIT_AUTHOR_NAME": self.committer_name,
            "GIT_AUTHOR_EMAIL": self.committer_email,
        }
        self._git_env.pop("GIT_DIR", None)
        self._git_env.pop("GIT_WORK_TREE", None)

    def run(self) -> dict:
        summary = self.manifest.get("summary", {})
        objects = self.manifest.get("objects", [])
        redirect_map = self.manifest.get("redirect_map", {})

        domain_groups = self._group_by_domain(objects)

        global_all_ids = {obj["domain_resource_id"] for obj in objects if obj.get("domain_resource_id")}
        global_path_to_id = {obj["destination_path"]: obj["domain_resource_id"] for obj in objects if obj.get("domain_resource_id")}
        global_id_to_path = {v: k for k, v in global_path_to_id.items()}

        results: dict[str, dict] = {}
        for dest_repo, domain_objects in domain_groups.items():
            domain_result = self._run_domain(
                dest_repo, domain_objects, redirect_map,
                global_all_ids, global_path_to_id, global_id_to_path
            )
            results[dest_repo] = domain_result

        return {
            "migration_run_id": self.manifest.get("migration_run_id", "unknown"),
            "summary": summary,
            "domain_results": results,
            "invariant_holds": all(r.get("invariant_holds", False) for r in results.values()),
        }

    def _group_by_domain(self, objects: list[dict]) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = {}
        for obj in objects:
            dest = obj.get("destination_repo", "default")
            groups.setdefault(dest, []).append(obj)
        return groups

    def _run_domain(
        self,
        dest_repo: str,
        objects: list[dict],
        redirect_map: dict,
        global_all_ids: set[str],
        global_path_to_id: dict[str, str],
        global_id_to_path: dict[str, str],
    ) -> dict:
        dest_path = self.dest_root / dest_repo.lstrip("/")
        dest_path.mkdir(parents=True, exist_ok=True)

        source_sets = self.manifest.get("source_sets", [])
        domain_source_sets = [
            ss for ss in source_sets
            if ss.get("destination_repo", ss.get("source_repo", "")) == dest_repo
        ]

        git_source_repos = [
            ss for ss in domain_source_sets
            if ss.get("source_commit", "0" * 40) != "0" * 40
            and ss.get("root") and Path(ss["root"]).exists()
        ]

        if git_source_repos:
            self._init_dest_repo_from_git(dest_path, git_source_repos)
        else:
            self._init_dest_repo_empty(dest_path)

        if len(domain_source_sets) > len(git_source_repos) and git_source_repos:
            for ss in domain_source_sets:
                if ss not in git_source_repos:
                    root = ss.get("root", "")
                    if not root or not Path(root).exists():
                        raise RehearsalError(
                            f"Cannot merge source set '{ss.get('name', 'unnamed')}': "
                            f"source root '{root}' does not exist"
                        )

        self._verify_path_filter(dest_path, objects)
        self._materialize_objects(dest_path, objects)
        self._apply_integrity_gate(dest_path, objects)

        references = self._resolve_references(objects, global_all_ids, global_path_to_id, global_id_to_path)

        commit_sha = self._create_final_commit(dest_path, objects, references, redirect_map)

        old_broken = sum(1 for ref in references if ref["disposition"] == DISPOSITION_BROKEN_OLD_ACK)
        new_broken = sum(1 for ref in references if ref["disposition"] == DISPOSITION_BROKEN_NEW)
        total_broken_before = sum(1 for ref in references if ref["old_target_id"] is None)
        total_broken_after = sum(1 for ref in references if ref["new_target_id"] is None)
        constraint_holds = (total_broken_after == total_broken_before)

        references_json = {
            "constraint_holds": constraint_holds,
            "old_broken_acknowledged": old_broken,
            "new_broken": new_broken,
            "entries": references,
        }
        self._emit_catalogs(dest_path, objects, redirect_map, references_json)

        return {
            "destination_repo": dest_repo,
            "final_commit": commit_sha,
            "object_count": len(objects),
            "reference_count": len(references),
            "old_broken_acknowledged": old_broken,
            "new_broken": new_broken,
            "constraint_holds": constraint_holds,
            "invariant_holds": constraint_holds,
        }

    def _init_dest_repo_empty(self, dest_path: Path) -> None:
        subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
        marker = dest_path / ".gitkeep"
        marker.write_text("")
        self._git_add_and_commit(dest_path, ["."], "Initial empty commit (rehearsal)")

    def _init_dest_repo_from_git(self, dest_path: Path, source_sets: list[dict]) -> None:
        if not source_sets:
            self._init_dest_repo_empty(dest_path)
            return

        primary = source_sets[0]
        source_root = primary.get("root", "")
        source_commit = primary.get("source_commit", "0" * 40)

        if source_root and Path(source_root).exists():
            if (Path(source_root) / ".git").exists():
                subprocess.run(
                    ["git", "clone", "--no-local", str(source_root), str(dest_path)],
                    check=True, capture_output=True
                )
                if source_commit and source_commit != "0" * 40:
                    try:
                        subprocess.run(
                            ["git", "checkout", source_commit],
                            cwd=str(dest_path), check=True, capture_output=True
                        )
                    except subprocess.CalledProcessError:
                        subprocess.run(
                            ["git", "checkout", "-b", "rehearsal-branch", source_commit],
                            cwd=str(dest_path), check=True, capture_output=True
                        )
            else:
                self._init_dest_repo_empty(dest_path)
                self._copy_source_tree(dest_path, source_root)
                self._git_add_and_commit(dest_path, ["."], "Import from source root")
        else:
            self._init_dest_repo_empty(dest_path)

        if len(source_sets) > 1:
            self._merge_source_repos(dest_path, source_sets)

    def _merge_source_repos(self, dest_path: Path, source_sets: list[dict]) -> None:
        for i, ss in enumerate(source_sets[1:], start=1):
            source_root = ss.get("root", "")
            source_commit = ss.get("source_commit", "0" * 40)
            remote_name = f"source_{i}"

            if not source_root or not Path(source_root).exists():
                raise RehearsalError(
                    f"Cannot merge source set {i}: source root {source_root} does not exist"
                )

            if not (Path(source_root) / ".git").exists():
                raise RehearsalError(
                    f"Cannot merge source set {i}: source root {source_root} is not a git repo"
                )

            try:
                subprocess.run(
                    ["git", "remote", "add", remote_name, str(source_root)],
                    cwd=str(dest_path), check=True, capture_output=True
                )
            except subprocess.CalledProcessError as e:
                raise RehearsalError(
                    f"Failed to add remote {remote_name} for source set {i}: {e}"
                ) from e

            try:
                subprocess.run(
                    ["git", "fetch", remote_name, "--tags"],
                    cwd=str(dest_path), check=True, capture_output=True
                )
            except subprocess.CalledProcessError as e:
                raise RehearsalError(
                    f"Failed to fetch from remote {remote_name} for source set {i}: {e}"
                ) from e

            if source_commit and source_commit != "0" * 40:
                fetch_ref = source_commit
            else:
                fetch_ref = f"{remote_name}/main"

            try:
                subprocess.run(
                    ["git", "merge", "--allow-unrelated-histories", "-m",
                     f"Merge source set {i} ({ss.get('name', 'unnamed')})",
                     fetch_ref],
                    cwd=str(dest_path), check=True, capture_output=True,
                    env={**os.environ, **self._git_env}
                )
            except subprocess.CalledProcessError as e:
                raise RehearsalError(
                    f"Merge failed for source set {i} ({ss.get('name', 'unnamed')}): {e}. "
                    f"Rehearsal halted — no silent degradation."
                ) from e

    def _copy_source_tree(self, dest_path: Path, source_root: str) -> None:
        src = Path(source_root)
        for item in src.rglob("*"):
            if item.name == ".git":
                continue
            rel = item.relative_to(src)
            target = dest_path / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file() and not item.is_symlink():
                shutil.copy2(item, target)

    def _verify_path_filter(self, dest_path: Path, objects: list[dict]) -> None:
        allowed_paths = {obj["destination_path"] for obj in objects}

        for root, dirs, files in os.walk(str(dest_path)):
            if ".git" in dirs:
                dirs.remove(".git")
            for fname in files:
                rel = str(Path(root).relative_to(dest_path) / fname)
                if rel == ".gitkeep":
                    continue
                if rel.startswith(".migration"):
                    continue
                if rel not in allowed_paths:
                    raise RehearsalError(
                        f"Path filter violation: '{rel}' exists in destination "
                        f"but is not in the manifest's object set. "
                        f"Rehearsal halted — no silent degradation."
                    )

    def _domain_paths(self, objects: list[dict]) -> set[str]:
        paths: set[str] = set()
        for obj in objects:
            paths.add(obj["destination_path"])
            parts = Path(obj["destination_path"]).parts
            for i in range(1, len(parts)):
                paths.add(str(Path(*parts[:i])))
        return paths

    def _materialize_objects(self, dest_path: Path, objects: list[dict]) -> None:
        for obj in objects:
            action = obj.get("action", ACTION_PRESERVE)
            if action == ACTION_REJECT:
                rejected_target = dest_path / obj["destination_path"]
                if rejected_target.is_file() or rejected_target.is_symlink():
                    rejected_target.unlink()
                continue
            target = self._materialized_path(dest_path, obj)

            target.parent.mkdir(parents=True, exist_ok=True)

            original_target = dest_path / obj["destination_path"]
            if target != original_target and original_target.is_file():
                original_target.unlink()

            source_root = self._find_source_root(obj)
            if source_root is None:
                raise RehearsalError(
                    f"No source root found for object {obj.get('source_path', '?')}"
                )

            source_file = Path(source_root) / obj["source_path"]
            if not source_file.exists():
                raise RehearsalError(
                    f"Source file not found: {source_file}"
                )

            try:
                content = source_file.read_bytes()
            except OSError as e:
                raise RehearsalError(
                    f"Failed to read source file {source_file}: {e}"
                ) from e

            self._materialize_one(target, obj, content, source_file)

    def _materialized_path(self, dest_path: Path, obj: dict) -> Path:
        action = obj.get("action", ACTION_PRESERVE)
        if action == ACTION_ARCHIVE:
            return dest_path / "_archive" / obj["destination_path"]
        if action == ACTION_QUARANTINE:
            return dest_path / "_quarantine" / obj["destination_path"]
        return dest_path / obj["destination_path"]

    def _find_source_root(self, obj: dict) -> str | None:
        source_repo = obj.get("source_repo", "")
        source_path = obj.get("source_path", "")
        candidates = []
        for i, ss in enumerate(self.manifest.get("source_sets", [])):
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

    def _materialize_one(self, target: Path, obj: dict, content: bytes, source_file: Path) -> None:
        action = obj.get("action", ACTION_PRESERVE)

        if action == ACTION_PRESERVE:
            self._apply_preserve(target, obj, content, source_file)
        elif action == ACTION_ID_BACKFILL:
            self._apply_id_backfill(target, obj, content, source_file)
        elif action == ACTION_NORMALIZE:
            self._apply_normalize(target, obj, content, source_file)
        elif action == ACTION_REWRITE:
            self._apply_rewrite(target, obj, content, source_file)
        elif action == ACTION_MERGE:
            self._apply_merge(target, obj, content, source_file)
        elif action == ACTION_ARCHIVE:
            self._apply_preserve(target, obj, content, source_file)
        elif action == ACTION_QUARANTINE:
            self._apply_preserve(target, obj, content, source_file)
        else:
            target.write_bytes(content)

        if action != ACTION_NORMALIZE:
            self._apply_declared_normalizations(target, obj)

    def _apply_preserve(self, target: Path, obj: dict, content: bytes, source_file: Path) -> None:
        target.write_bytes(content)
        actual_sha = _sha256_hex(content)
        expected_sha = obj.get("sha256") or obj.get("pre_hash")
        if expected_sha and actual_sha != expected_sha:
            raise RehearsalError(
                f"SHA-256 mismatch for {obj['destination_path']}: "
                f"expected {expected_sha}, got {actual_sha}"
            )
        if source_file.exists():
            st = source_file.stat()
            target.chmod(st.st_mode)

    def _apply_id_backfill(self, target: Path, obj: dict, content: bytes, source_file: Path) -> None:
        resource_id = obj.get("domain_resource_id", "")
        if not resource_id:
            raise RehearsalError(
                f"No domain_resource_id for id_backfill object {obj['destination_path']}"
            )

        orig_body = _extract_body_bytes(content)
        fm_end = _FRONTMATTER_END_RE.search(content, 4) if content.startswith(b"---\n") else None

        if fm_end is not None:
            frontmatter = content[4:fm_end.start() + 1]
            parsed_fm, _, _ = _parse_frontmatter(content)
            has_id = (
                (parsed_fm is not None and "id" in parsed_fm)
                or _FRONTMATTER_ID_RE.search(frontmatter) is not None
            )
            if has_id:
                if parsed_fm is not None and str(parsed_fm.get("id")) != resource_id:
                    raise RehearsalError(
                        f"Existing id does not match domain_resource_id for {obj['destination_path']}"
                    )
                new_content = content
            else:
                id_line = f"id: {resource_id}\n".encode("utf-8")
                new_content = content[:4] + id_line + content[4:]
        else:
            id_line = f"id: {resource_id}\n".encode("utf-8")
            new_content = b"---\n" + id_line + b"---\n" + content

        new_body = _extract_body_bytes(new_content)
        if new_body != orig_body:
            raise RehearsalError(
                f"ID backfill altered body bytes for {obj['destination_path']}"
            )

        target.write_bytes(new_content)

    def _apply_normalize(self, target: Path, obj: dict, content: bytes, source_file: Path) -> None:
        object_class = obj.get("object_class", "unknown")
        normalizations = obj.get("normalizations", [])
        fm, body, _ = _parse_frontmatter(content)

        diff_manifest: dict[str, object] = {
            "action": "normalize",
            "destination_path": obj["destination_path"],
            "changes": {},
        }

        if normalizations:
            new_content = content
        elif fm is not None:
            canonical_keys = _canonical_frontmatter_keys(object_class)
            if canonical_keys:
                ordered = {}
                for key in canonical_keys:
                    if key in fm:
                        ordered[key] = fm.pop(key)
                for key in sorted(fm.keys()):
                    ordered[key] = fm[key]
                if list(ordered.keys()) != list(canonical_keys) + sorted(set(fm.keys()) - set(canonical_keys)):
                    diff_manifest["changes"]["key_ordering"] = "reordered"
                fm = ordered

            defaults = _required_frontmatter_defaults(object_class)
            for key, default_val in defaults.items():
                if key not in fm:
                    fm[key] = default_val
                    diff_manifest["changes"][f"added_{key}"] = default_val

            new_content = _write_frontmatter(fm, body)
        else:
            new_content = content

        if NORMALIZE_UNICODE_NFC in normalizations and not _is_nfc_normalized(new_content):
            new_content = unicodedata.normalize("NFC", new_content.decode("utf-8")).encode("utf-8")
            diff_manifest["changes"]["unicode_normalization"] = "applied_NFC"

        target.write_bytes(new_content)
        if NORMALIZE_EXECUTABLE in normalizations:
            target.chmod(target.stat().st_mode & ~0o111)
            diff_manifest["changes"]["executable_bit"] = "cleared"

        diff_path = target.parent / f"{target.name}.diff_manifest.json"
        diff_path.write_text(json.dumps(diff_manifest, indent=2, sort_keys=True, ensure_ascii=False))

    def _apply_declared_normalizations(self, target: Path, obj: dict) -> None:
        normalizations = obj.get("normalizations", obj.get("allowed_transformations", []))
        if NORMALIZE_UNICODE_NFC in normalizations:
            content = target.read_bytes()
            try:
                normalized = unicodedata.normalize("NFC", content.decode("utf-8")).encode("utf-8")
            except UnicodeDecodeError:
                normalized = content
            target.write_bytes(normalized)
        if NORMALIZE_EXECUTABLE in normalizations:
            target.chmod(target.stat().st_mode & ~0o111)

    def _apply_rewrite(self, target: Path, obj: dict, content: bytes, source_file: Path) -> None:
        object_class = obj.get("object_class", "unknown")
        fm, body, _ = _parse_frontmatter(content)

        diff_manifest: dict[str, object] = {
            "action": "rewrite",
            "destination_path": obj["destination_path"],
            "changes": {},
        }

        redirect_map = self.manifest.get("redirect_map", {})
        rewrites = obj.get("reference_rewrites", [])

        if fm is not None:
            canonical_keys = _canonical_frontmatter_keys(object_class)
            if canonical_keys:
                ordered = {}
                for key in canonical_keys:
                    if key in fm:
                        ordered[key] = fm.pop(key)
                for key in sorted(fm.keys()):
                    ordered[key] = fm[key]
                fm = ordered

            defaults = _required_frontmatter_defaults(object_class)
            for key, default_val in defaults.items():
                if key not in fm:
                    fm[key] = default_val

            new_content = _write_frontmatter(fm, body)
        else:
            new_content = content

        try:
            text = new_content.decode("utf-8")
        except UnicodeDecodeError:
            target.write_bytes(new_content)
            return

        rewrite_count = 0
        for rw in rewrites:
            old = rw.get("old", "")
            new = rw.get("new", "")
            if old and old in text:
                text = text.replace(old, new)
                rewrite_count += 1

        if rewrite_count > 0:
            diff_manifest["changes"]["links_rewritten"] = rewrite_count

        if not _is_nfc_normalized(text.encode("utf-8")):
            text = unicodedata.normalize("NFC", text)
            diff_manifest["changes"]["unicode_normalization"] = "applied_NFC"

        new_content = text.encode("utf-8")
        target.write_bytes(new_content)

        diff_path = target.parent / f"{target.name}.diff_manifest.json"
        diff_path.write_text(json.dumps(diff_manifest, indent=2, sort_keys=True, ensure_ascii=False))

    def _apply_merge(self, target: Path, obj: dict, content: bytes, source_file: Path) -> None:
        if target.exists():
            existing = target.read_bytes()
            if existing == content:
                return
            existing_fm, existing_body, _ = _parse_frontmatter(existing)
            new_fm, new_body, _ = _parse_frontmatter(content)

            if existing_fm is not None and new_fm is not None:
                merged_fm = dict(existing_fm)
                for k, v in new_fm.items():
                    if k not in merged_fm:
                        merged_fm[k] = v
                    elif k in ("tags", "related") and isinstance(v, list) and isinstance(merged_fm.get(k), list):
                        existing_list = merged_fm[k]
                        for item in v:
                            if item not in existing_list:
                                existing_list.append(item)
                merged_body = new_body if new_body else existing_body
                merged = _write_frontmatter(merged_fm, merged_body)
                target.write_bytes(merged)
            else:
                target.write_bytes(content)
        else:
            target.write_bytes(content)

    def _apply_integrity_gate(self, dest_path: Path, objects: list[dict]) -> None:
        errors: list[dict] = []
        dest_str = str(dest_path)
        flat_casefold: dict[str, str] = {}
        path_casefold: dict[str, str] = {}

        for obj in objects:
            if obj.get("action") == ACTION_REJECT:
                continue

            target = self._materialized_path(dest_path, obj)
            if not target.exists():
                continue

            if target.is_symlink():
                errors.append({
                    "code": GATE_SYMLINK,
                    "path": obj["destination_path"],
                    "reason": "Symlinks are rejected by default",
                })
                continue

            try:
                content = target.read_bytes()
            except OSError as e:
                errors.append({
                    "code": "READ_ERROR",
                    "path": obj["destination_path"],
                    "reason": str(e),
                })
                continue

            preservation_modes = set(obj.get("preservation_modes", []))
            if _is_binary(content) and "binary_bytes" not in preservation_modes:
                errors.append({
                    "code": GATE_BINARY,
                    "path": obj["destination_path"],
                    "reason": "File contains binary bytes",
                })

            if _is_lfs_pointer(content) and "lfs_pointer" not in preservation_modes:
                errors.append({
                    "code": GATE_LFS,
                    "path": obj["destination_path"],
                    "reason": "File is a git LFS pointer",
                })

            st = target.stat()
            if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                errors.append({
                    "code": GATE_EXECUTABLE,
                    "path": obj["destination_path"],
                    "reason": "File has executable bit set",
                })

            basename = Path(obj["destination_path"]).name
            if len(basename.encode("utf-8")) > MAX_BASENAME_LENGTH:
                errors.append({
                    "code": GATE_PATH_LENGTH,
                    "path": obj["destination_path"],
                    "reason": f"Basename exceeds {MAX_BASENAME_LENGTH} bytes",
                })

            if len(obj["destination_path"].encode("utf-8")) > MAX_PATH_LENGTH:
                errors.append({
                    "code": GATE_PATH_LENGTH,
                    "path": obj["destination_path"],
                    "reason": f"Path exceeds {MAX_PATH_LENGTH} bytes",
                })

            if obj.get("object_class") in _PATH_PRESERVING_CLASSES:
                collision_key = unicodedata.normalize("NFC", obj["destination_path"]).casefold()
                previous = path_casefold.get(collision_key)
                if previous is not None and previous != obj["destination_path"]:
                    errors.append({
                        "code": GATE_CASEFOLD,
                        "path": obj["destination_path"],
                        "reason": f"Casefold collision with {previous}: {obj['destination_path']}",
                    })
                else:
                    path_casefold[collision_key] = obj["destination_path"]
            else:
                collision_key = unicodedata.normalize("NFC", basename).casefold()
                previous = flat_casefold.get(collision_key)
                if previous is not None and previous != basename:
                    errors.append({
                        "code": GATE_CASEFOLD,
                        "path": obj["destination_path"],
                        "reason": f"Casefold collision with {previous}: {basename}",
                    })
                else:
                    flat_casefold[collision_key] = basename

            if not _is_nfc_normalized(content):
                errors.append({
                    "code": GATE_UNICODE_NFC,
                    "path": obj["destination_path"],
                    "reason": "Content is not NFC normalized",
                })

        if errors:
            error_codes = {e["code"] for e in errors}
            raise RehearsalError(
                f"Integrity gate failed with {len(errors)} error(s): "
                f"{', '.join(sorted(error_codes))}. "
                f"Rehearsal halted — no silent degradation."
            )

    def _resolve_references(
        self,
        objects: list[dict],
        all_ids: set[str],
        path_to_id: dict[str, str],
        id_to_path: dict[str, str],
    ) -> list[dict]:
        references: list[dict] = []
        redirect_map = self.manifest.get("redirect_map", {})

        for obj in objects:
            if obj.get("action") == ACTION_REJECT:
                continue

            source_root = self._find_source_root(obj)
            if source_root is None:
                continue

            source_file = Path(source_root) / obj["source_path"]
            if not source_file.exists():
                continue

            try:
                content = source_file.read_bytes()
            except OSError:
                continue

            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue

            source_id = obj.get("domain_resource_id", "")
            extracted = _extract_references_from_text(text)

            for ref in extracted:
                old_literal = ref["old_literal"]
                old_target = ref["old_target"]
                anchor = ref.get("anchor")

                old_target_id = self._resolve_target(old_target, path_to_id, all_ids)
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

                new_target_id = self._resolve_target(new_target, path_to_id, all_ids)
                new_resolved = (new_target_id is not None)

                if old_resolved and new_resolved:
                    if old_target_id == new_target_id:
                        disposition = DISPOSITION_RESOLVED
                    else:
                        disposition = DISPOSITION_REDIRECTED
                elif not old_resolved:
                    disposition = DISPOSITION_BROKEN_OLD_ACK
                elif old_resolved and not new_resolved:
                    disposition = DISPOSITION_BROKEN_NEW
                else:
                    disposition = DISPOSITION_BROKEN_OLD_ACK

                references.append({
                    "source_id": source_id,
                    "source_path": obj["source_path"],
                    "old_literal": old_literal,
                    "old_target_id": old_target_id,
                    "new_target_id": new_target_id,
                    "anchor": anchor,
                    "disposition": disposition,
                    "link_type": ref["link_type"],
                })

        return references

    def _resolve_target(
        self,
        target: str,
        path_to_id: dict[str, str],
        all_ids: set[str],
    ) -> str | None:
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

    def _create_final_commit(
        self,
        dest_path: Path,
        objects: list[dict],
        references: list[dict],
        redirect_map: dict,
    ) -> str:
        old_broken = sum(1 for ref in references if ref["disposition"] == DISPOSITION_BROKEN_OLD_ACK)
        new_broken = sum(1 for ref in references if ref["disposition"] == DISPOSITION_BROKEN_NEW)
        constraint_holds = (new_broken - old_broken) == 0

        marker = {
            "marker": "MIGRATION_BASE",
            "migration_run_id": self.manifest.get("migration_run_id", "unknown"),
            "object_count": len(objects),
            "reference_count": len(references),
            "reference_constraint_holds": constraint_holds,
        }
        marker_path = dest_path / "MIGRATION_BASE.json"
        marker_path.write_text(json.dumps(marker, indent=2, sort_keys=True, ensure_ascii=False))

        self._git_add_and_commit(
            dest_path,
            ["."],
            f"Rehearsal final maintenance commit — {len(objects)} objects, "
            f"reference constraint holds: {constraint_holds}"
        )

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(dest_path), check=True, capture_output=True, text=True
        )
        return result.stdout.strip()

    def _emit_catalogs(
        self,
        dest_path: Path,
        objects: list[dict],
        redirect_map: dict,
        references_json: dict,
    ) -> None:
        redirects = {}
        for obj in objects:
            if obj.get("action") == ACTION_ID_BACKFILL and obj.get("domain_resource_id"):
                redirects[obj["source_path"]] = obj["domain_resource_id"]
        for src_path, new_id in redirect_map.items():
            if src_path not in redirects:
                redirects[src_path] = new_id

        redirects_path = dest_path / "redirects.json"
        redirects_path.write_text(json.dumps(redirects, indent=2, sort_keys=True, ensure_ascii=False))

        refs_path = dest_path / "references.json"
        refs_path.write_text(json.dumps(references_json, indent=2, sort_keys=True, ensure_ascii=False))

    def _git_add_and_commit(self, dest_path: Path, paths: list[str], message: str) -> None:
        subprocess.run(
            ["git", "add"] + paths,
            cwd=str(dest_path), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=str(dest_path), check=True, capture_output=True,
            env={**os.environ, **self._git_env}
        )


# ── Public API ────────────────────────────────────────────────────────────────

def run_rehearsal(
    manifest: dict,
    dest_root: str,
    *,
    committer_date: str | None = None,
    committer_name: str = _DEFAULT_COMMITTER_NAME,
    committer_email: str = _DEFAULT_COMMITTER_EMAIL,
) -> dict:
    engine = RehearsalEngine(
        manifest,
        dest_root,
        committer_date=committer_date,
        committer_name=committer_name,
        committer_email=committer_email,
    )
    return engine.run()
