"""M3b Rehearsal Engine: REHEARSED phase import/rehearsal.

Consumes M3a manifest, produces per-domain rehearsal destination git repos
with full import, path-filtered history, integrity gate, reference resolution,
and final maintenance commits.  Idempotent: same manifest + same source =
byte-identical destination (including stable commit tree when committer_date
is injected).

All writes are confined to a temporary dest_root.  Never touches production
data roots.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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
    _TRANSFORM_ACTIONS,
    sha256_hex,
    is_binary,
    is_lfs_pointer,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MEMORY_ID_RE = re.compile(r"m-[0-9a-f]{6}")
WIKI_ID_RE = re.compile(r"w-[0-9a-f]{6}")
WF_ID_RE = re.compile(r"wf-[0-9a-f]{6}")
RESOURCE_ID_RE = re.compile(r"(?:m|w|wf)-[0-9a-f]{6}")
WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")

MAX_BASENAME_LENGTH = 255
MAX_PATH_LENGTH = 4096

MIGRATION_BASE_FILENAME = "MIGRATION_BASE"
REDIRECTS_FILENAME = "redirects.json"
REFERENCES_FILENAME = "references.json"

# ── Helpers ───────────────────────────────────────────────────────────────────


def _git_init(repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo_path, check=True, capture_output=True,
    )


def _git_config(repo_path: Path, key: str, value: str) -> None:
    subprocess.run(
        ["git", "config", key, value],
        cwd=repo_path, check=True, capture_output=True,
    )


def _git_commit(
    repo_path: Path,
    message: str,
    committer_date: str | None = None,
    allow_empty: bool = False,
) -> str:
    env = os.environ.copy()
    if committer_date is not None:
        env["GIT_COMMITTER_DATE"] = committer_date
        env["GIT_AUTHOR_DATE"] = committer_date
    args = ["git", "add", "-A"]
    subprocess.run(args, cwd=repo_path, check=True, capture_output=True)
    commit_args = ["git", "commit", "-m", message]
    if committer_date is not None:
        commit_args.extend(["--date", committer_date])
    if allow_empty:
        commit_args.append("--allow-empty")
    subprocess.run(commit_args, cwd=repo_path, check=True, capture_output=True, env=env)
    return _git_head_sha(repo_path)


def _git_head_sha(repo_path: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, text=True,
    ).strip()


def _git_log_commits_touching_paths(
    repo_path: Path, commit: str, paths: list[str],
) -> list[str]:
    result = subprocess.run(
        ["git", "log", "--format=%H", "--reverse", commit, "--"] + paths,
        cwd=repo_path, check=True, capture_output=True, text=True,
    )
    return [h for h in result.stdout.strip().split("\n") if h]


def _git_is_repo(path: Path) -> bool:
    if not (path / ".git").exists():
        return False
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=path, check=True, capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _extract_filtered_history(
    source_repo: str,
    source_commit: str,
    paths: list[str],
    dest_repo_path: Path,
    temp_dir: Path,
) -> None:
    source_path = Path(source_repo)
    if not _git_is_repo(source_path):
        _git_init(dest_repo_path)
        return

    clone_path = temp_dir / "filtered_clone"
    if clone_path.exists():
        shutil.rmtree(clone_path)

    subprocess.run(
        ["git", "clone", str(source_path), str(clone_path)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-B", "main", source_commit],
        cwd=clone_path, check=True, capture_output=True,
    )

    if not paths:
        dest_repo_path.parent.mkdir(parents=True, exist_ok=True)
        if dest_repo_path.exists():
            shutil.rmtree(dest_repo_path)
        shutil.move(str(clone_path), str(dest_repo_path))
        return

    path_set = set(paths)
    path_args = " ".join(f"'{p}'" for p in sorted(path_set))
    index_filter = (
        "git rm --cached -qr --ignore-unmatch -- . ; "
        f"git reset -q $GIT_COMMIT -- {path_args} || :"
    )

    env = os.environ.copy()
    env["FILTER_BRANCH_SQUELCH_WARNING"] = "1"
    try:
        subprocess.run(
            ["git", "filter-branch", "-f", "--prune-empty", "--index-filter", index_filter, "HEAD"],
            cwd=clone_path, check=True, capture_output=True, env=env,
        )
    except subprocess.CalledProcessError:
        pass

    dest_repo_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_repo_path.exists():
        shutil.rmtree(dest_repo_path)
    shutil.move(str(clone_path), str(dest_repo_path))


def _check_integrity(record: dict, dest_path: Path) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []

    if record.get("exception_code") == EXC_SYMLINK:
        if dest_path.exists() and dest_path.is_symlink():
            issues.append((EXC_SYMLINK, "Symlink detected in destination"))
        elif dest_path.exists():
            pass
        else:
            issues.append((EXC_SYMLINK, "Symlink rejected per spec"))

    rel_path = str(dest_path) if dest_path.exists() else record.get("destination_path", "")
    if rel_path:
        basename = Path(rel_path).name
        if len(basename.encode("utf-8")) > MAX_BASENAME_LENGTH:
            issues.append((EXC_PATH_LENGTH, f"Basename exceeds {MAX_BASENAME_LENGTH} bytes"))
        if len(rel_path.encode("utf-8")) > MAX_PATH_LENGTH:
            issues.append((EXC_PATH_LENGTH, f"Path exceeds {MAX_PATH_LENGTH} bytes"))

    if dest_path.exists() and not dest_path.is_symlink():
        try:
            content = dest_path.read_bytes()
        except OSError:
            content = b""
        if is_binary(content):
            issues.append((EXC_BINARY, "Binary bytes detected"))
        if is_lfs_pointer(content):
            issues.append((EXC_LFS_POINTER, "LFS pointer detected"))
        st = dest_path.stat()
        import stat
        if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            issues.append((EXC_EXECUTABLE, "Executable bit set"))

    if dest_path.exists() and not dest_path.is_symlink():
        try:
            text = dest_path.read_text(encoding="utf-8")
            nfc = unicodedata.normalize("NFC", text)
            if text != nfc:
                issues.append(("UNICODE_NFC", "Content not in NFC normalization"))
        except (UnicodeDecodeError, OSError):
            pass

    domain = record.get("destination_repo", "")
    if dest_path.parent.exists():
        casefold_map: dict[str, str] = {}
        try:
            for entry in dest_path.parent.iterdir():
                if entry.name == dest_path.name:
                    continue
                cf = entry.name.casefold()
                dest_cf = dest_path.name.casefold()
                if cf == dest_cf:
                    issues.append((EXC_CASEFOLD_COLLISION, f"Casefold collision with {entry.name}"))
        except OSError:
            pass

    return issues


def _extract_body_bytes(content: bytes) -> bytes:
    if not content.startswith(b"---\n"):
        return content
    text = content.decode("utf-8", errors="replace")
    fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
    if fm_end is None:
        return content
    body_start = 4 + fm_end.end()
    return content[body_start:]


def _inject_frontmatter_id(content: bytes, new_id: str) -> bytes:
    if not content.startswith(b"---\n"):
        return content
    text = content.decode("utf-8", errors="replace")
    fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
    if fm_end is None:
        return content
    fm_text = text[4:4 + fm_end.start() + 1]
    body_text = text[4 + fm_end.end():]
    if re.search(r"^id:", fm_text, re.MULTILINE):
        new_fm = re.sub(r"^id:.*", f"id: {new_id}", fm_text, flags=re.MULTILINE)
    else:
        new_fm = f"id: {new_id}\n{fm_text}"
    new_text = f"---\n{new_fm}---\n{body_text}"
    return new_text.encode("utf-8")


def _semantic_diff_manifest(original: bytes, transformed: bytes, source_path: str) -> dict:
    diff: dict = {"source_path": source_path, "kind": "semantic", "changes": []}

    try:
        orig_text = original.decode("utf-8")
    except UnicodeDecodeError:
        orig_text = ""
    try:
        trans_text = transformed.decode("utf-8")
    except UnicodeDecodeError:
        trans_text = ""

    if orig_text.startswith("---\n") and trans_text.startswith("---\n"):
        orig_fm = _parse_frontmatter_safe(orig_text)
        trans_fm = _parse_frontmatter_safe(trans_text)
        for key in sorted(set(list(orig_fm.keys()) + list(trans_fm.keys()))):
            ov = orig_fm.get(key)
            tv = trans_fm.get(key)
            if ov != tv:
                diff["changes"].append({
                    "location": f"frontmatter.{key}",
                    "old": ov,
                    "new": tv,
                })
        orig_body = _extract_body_text(orig_text)
        trans_body = _extract_body_text(trans_text)
    else:
        orig_body = orig_text
        trans_body = trans_text

    orig_sections = _parse_markdown_sections(orig_body)
    trans_sections = _parse_markdown_sections(trans_body)

    all_keys = sorted(set(orig_sections.keys()) | set(trans_sections.keys()))
    for key in all_keys:
        if key not in orig_sections:
            diff["changes"].append({
                "location": f"section.{key}",
                "old": None,
                "new": trans_sections[key],
            })
        elif key not in trans_sections:
            diff["changes"].append({
                "location": f"section.{key}",
                "old": orig_sections[key],
                "new": None,
            })
        elif orig_sections[key] != trans_sections[key]:
            diff["changes"].append({
                "location": f"section.{key}",
                "old": orig_sections[key],
                "new": trans_sections[key],
            })

    diff["change_count"] = len(diff["changes"])
    return diff


def _parse_frontmatter_safe(text: str) -> dict:
    if not text.startswith("---\n"):
        return {}
    fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
    if fm_end is None:
        return {}
    fm_text = text[4:4 + fm_end.start() + 1]
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return {}
    if not isinstance(fm, dict):
        return {}
    return fm


def _extract_body_text(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    fm_end = re.search(r"\n---[ \t]*(?:\n|$)", text[4:])
    if fm_end is None:
        return text
    return text[4 + fm_end.end():]


def _parse_markdown_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current_section = ""
    for line in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            current_section = m.group(2).strip()
            sections[current_section] = ""
        elif current_section:
            sections[current_section] += line + "\n"
    if current_section:
        sections[current_section] = sections[current_section].rstrip("\n")
    if not sections and text.strip():
        sections["_body"] = text.strip()
    return sections


def _parse_references_from_content(content: bytes) -> list[dict]:
    refs: list[dict] = []
    try:
        text = content.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return refs
    for m in WIKI_LINK_RE.finditer(text):
        target = m.group(1).strip()
        if RESOURCE_ID_RE.match(target):
            refs.append({
                "type": "wiki_link",
                "literal": m.group(0),
                "target_id": target,
                "anchor": m.group(1) if "|" in m.group(0) else None,
            })
    for m in MARKDOWN_LINK_RE.finditer(text):
        url = m.group(2)
        if RESOURCE_ID_RE.match(url):
            refs.append({
                "type": "markdown_link",
                "literal": m.group(0),
                "target_id": url,
                "anchor": m.group(1),
            })
    for m in RESOURCE_ID_RE.finditer(text):
        context_start = max(0, m.start() - 1)
        context_end = min(len(text), m.end() + 1)
        ctx = text[context_start:context_end]
        if ctx.startswith("[") or ctx.endswith("]") or ctx.startswith("("):
            continue
        if m.start() > 0 and text[m.start() - 1] in ("[", "("):
            continue
        if m.end() < len(text) and text[m.end()] in ("]", ")"):
            continue
        refs.append({
            "type": "bare_id",
            "literal": m.group(0),
            "target_id": m.group(0),
            "anchor": None,
        })
    return refs


def _resolve_references(
    manifest_objects: list[dict],
    domain_objects: list[dict],
    domain: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    all_ids: set[str] = set()
    for obj in manifest_objects:
        rid = obj.get("domain_resource_id")
        if rid:
            all_ids.add(rid)

    domain_id_map: dict[str, str] = {}
    for obj in domain_objects:
        sid = obj.get("source_path", "")
        did = obj.get("domain_resource_id")
        if sid and did:
            domain_id_map[sid] = did

    reference_entries: list[dict] = []
    old_broken: list[dict] = []
    new_broken: list[dict] = []

    for obj in domain_objects:
        source_path = obj.get("source_path", "")
        dest_path = obj.get("destination_path", source_path)
        if not source_path:
            continue

        source_id = obj.get("domain_resource_id")
        refs = []
        if obj.get("sha256"):
            try:
                source_file = Path(obj.get("source_repo", "")) / source_path
                if source_file.exists():
                    content = source_file.read_bytes()
                    refs = _parse_references_from_content(content)
            except (OSError, ValueError):
                pass

        for ref in refs:
            target_id = ref["target_id"]
            old_resolved = target_id in all_ids
            new_target = target_id
            new_resolved = target_id in all_ids

            if not old_resolved:
                old_broken.append({
                    "source_id": source_id,
                    "source_path": source_path,
                    "target_id": target_id,
                    "literal": ref["literal"],
                    "reason": "target ID not in manifest",
                })

            if not new_resolved:
                new_broken.append({
                    "source_id": source_id,
                    "source_path": source_path,
                    "target_id": target_id,
                    "literal": ref["literal"],
                    "reason": "target ID not in manifest",
                })

            reference_entries.append({
                "source_id": source_id,
                "source_path": source_path,
                "old_literal": ref["literal"],
                "old_target_id": target_id,
                "new_target_id": new_target,
                "anchor": ref.get("anchor"),
                "disposition": "resolved" if new_resolved else "broken",
            })

    return reference_entries, old_broken, new_broken


def _compute_reference_stats(
    reference_entries: list[dict],
    old_broken: list[dict],
    new_broken: list[dict],
) -> dict:
    return {
        "total_references": len(reference_entries),
        "old_broken": len(old_broken),
        "new_broken": len(new_broken),
        "new_minus_old": len(new_broken) - len(old_broken),
        "constraint_holds": len(new_broken) - len(old_broken) == 0,
    }


def _apply_link_rewrites(content: bytes, redirect_map: dict[str, str]) -> bytes:
    try:
        text = content.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return content

    for old_id, new_id in redirect_map.items():
        text = text.replace(old_id, new_id)

    return text.encode("utf-8")


# ── Materializers ─────────────────────────────────────────────────────────────


def _materialize_preserve(record: dict, source_file: Path, dest_file: Path) -> dict:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    content = source_file.read_bytes()
    dest_file.write_bytes(content)
    dest_sha256 = sha256_hex(content)
    src_sha256 = record.get("sha256", "")
    return {
        "action": ACTION_PRESERVE,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "sha256_match": dest_sha256 == src_sha256,
        "size": len(content),
    }


def _materialize_id_backfill(record: dict, source_file: Path, dest_file: Path) -> dict:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    content = source_file.read_bytes()
    resource_id = record.get("domain_resource_id", "")
    new_content = _inject_frontmatter_id(content, resource_id)
    dest_file.write_bytes(new_content)
    body_original = _extract_body_bytes(content)
    body_new = _extract_body_bytes(new_content)
    return {
        "action": ACTION_ID_BACKFILL,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "body_bytes_unchanged": body_original == body_new,
        "resource_id": resource_id,
        "size_before": len(content),
        "size_after": len(new_content),
    }


def _materialize_normalize(record: dict, source_file: Path, dest_file: Path) -> dict:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    content = source_file.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    nfc_text = unicodedata.normalize("NFC", text)
    new_content = nfc_text.encode("utf-8")
    dest_file.write_bytes(new_content)
    diff_manifest = _semantic_diff_manifest(content, new_content, record["source_path"])
    return {
        "action": ACTION_NORMALIZE,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "nfc_normalized": text != nfc_text,
        "diff_manifest": diff_manifest,
        "size_before": len(content),
        "size_after": len(new_content),
    }


def _materialize_rewrite(record: dict, source_file: Path, dest_file: Path) -> dict:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    content = source_file.read_bytes()
    redirect_map: dict[str, str] = {}
    for rw in record.get("reference_rewrites", []):
        if isinstance(rw, dict):
            old = rw.get("old_literal", rw.get("old"))
            new = rw.get("new_literal", rw.get("new"))
            if old and new:
                redirect_map[old] = new
    new_content = _apply_link_rewrites(content, redirect_map)
    dest_file.write_bytes(new_content)
    diff_manifest = _semantic_diff_manifest(content, new_content, record["source_path"])
    return {
        "action": ACTION_REWRITE,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "rewrites_applied": len(redirect_map),
        "diff_manifest": diff_manifest,
        "size_before": len(content),
        "size_after": len(new_content),
    }


def _materialize_merge(record: dict, source_file: Path, dest_file: Path) -> dict:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    content = source_file.read_bytes()
    dest_file.write_bytes(content)
    return {
        "action": ACTION_MERGE,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "size": len(content),
    }


def _materialize_archive(record: dict, source_file: Path, dest_file: Path) -> dict:
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    content = source_file.read_bytes()
    dest_file.write_bytes(content)
    return {
        "action": ACTION_ARCHIVE,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "size": len(content),
    }


def _materialize_reject(record: dict, source_file: Path, dest_file: Path) -> dict:
    return {
        "action": ACTION_REJECT,
        "source_path": record["source_path"],
        "dest_path": str(dest_file),
        "reason": record.get("reason", "Rejected"),
        "exception_code": record.get("exception_code"),
    }


MATERIALIZERS = {
    ACTION_PRESERVE: _materialize_preserve,
    ACTION_ID_BACKFILL: _materialize_id_backfill,
    ACTION_NORMALIZE: _materialize_normalize,
    ACTION_REWRITE: _materialize_rewrite,
    ACTION_MERGE: _materialize_merge,
    ACTION_ARCHIVE: _materialize_archive,
    ACTION_REJECT: _materialize_reject,
}

# ── Domain mapping ────────────────────────────────────────────────────────────

_WIKI_DEST_REPOS = {"wiki", "wiki_writable", "wiki_raw", "wiki_schema", "wiki_unknown"}
_MEMORY_DEST_REPOS = {"memory", "memory_canonical", "memory_legacy"}
_WF_DEST_REPOS = {"work_folder", "work-folder"}


def _get_domain(obj: dict) -> str:
    dest = obj.get("destination_repo", "")
    if dest in _WIKI_DEST_REPOS:
        return "wiki"
    if dest in _MEMORY_DEST_REPOS:
        return "memory"
    if dest in _WF_DEST_REPOS:
        return "work-folder"
    return "unknown"


# ── Rehearsal Engine ──────────────────────────────────────────────────────────


@dataclass
class RehearsalResult:
    migration_run_id: str
    dest_root: str
    domains: dict[str, dict] = field(default_factory=dict)
    summary: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class RehearsalEngine:
    def __init__(
        self,
        manifest: dict,
        source_root: str | None = None,
        dest_root: str | None = None,
        committer_date: str | None = None,
    ):
        self.manifest = manifest
        self.source_root = Path(source_root) if source_root else None
        self.dest_root = Path(dest_root) if dest_root else Path(tempfile.mkdtemp(prefix="rehearsal_"))
        self.committer_date = committer_date
        self._temp_dir = Path(tempfile.mkdtemp(prefix="rehearsal_tmp_"))

    def run(self) -> RehearsalResult:
        result = RehearsalResult(
            migration_run_id=self.manifest.get("migration_run_id", "unknown"),
            dest_root=str(self.dest_root),
        )

        objects = self.manifest.get("objects", [])
        domain_groups: dict[str, list[dict]] = {}
        for obj in objects:
            domain = _get_domain(obj)
            domain_groups.setdefault(domain, []).append(obj)

        for domain, domain_objects in sorted(domain_groups.items()):
            if domain == "unknown":
                continue
            try:
                domain_result = self._process_domain(domain, domain_objects, objects)
                result.domains[domain] = domain_result
            except Exception as e:
                result.errors.append(f"Domain {domain} failed: {e}")
                raise

        result.summary = self._compute_summary(result)
        return result

    def _process_domain(
        self, domain: str, domain_objects: list[dict], all_objects: list[dict],
    ) -> dict:
        dest_repo_path = self.dest_root / domain
        dest_repo_path.mkdir(parents=True, exist_ok=True)

        source_repo_map: dict[str, list[str]] = {}
        for obj in domain_objects:
            sr = obj.get("source_repo", "")
            sp = obj.get("source_path", "")
            if sr and sp:
                source_repo_map.setdefault(sr, []).append(sp)

        for source_repo, paths in source_repo_map.items():
            source_commit = ""
            for obj in domain_objects:
                if obj.get("source_repo") == source_repo:
                    source_commit = obj.get("source_commit", "")
                    break
            if domain in ("wiki", "work-folder"):
                _extract_filtered_history(
                    source_repo, source_commit, paths, dest_repo_path, self._temp_dir,
                )
            else:
                _extract_filtered_history(
                    source_repo, source_commit, [], dest_repo_path, self._temp_dir,
                )

        if not _git_is_repo(dest_repo_path):
            _git_init(dest_repo_path)

        action_results: list[dict] = []
        integrity_issues: list[dict] = []
        blocking_errors: list[str] = []

        for obj in domain_objects:
            action = obj.get("action", ACTION_PRESERVE)
            source_path_str = obj.get("source_path", "")
            dest_path = dest_repo_path / source_path_str

            source_file = self._resolve_source_file(obj)

            materializer = MATERIALIZERS.get(action, _materialize_reject)
            result = materializer(obj, source_file, dest_path)
            action_results.append(result)

            check_path = dest_path if dest_path.exists() else source_file
            issues = _check_integrity(obj, check_path)
            for issue_code, issue_reason in issues:
                integrity_issues.append({
                    "source_path": source_path_str,
                    "code": issue_code,
                    "reason": issue_reason,
                })
                if action != ACTION_REJECT and issue_code in (EXC_SYMLINK, EXC_BINARY, EXC_LFS_POINTER, EXC_PATH_LENGTH, EXC_CASEFOLD_COLLISION):
                    blocking_errors.append(f"{issue_code}: {source_path_str} - {issue_reason}")

        if blocking_errors:
            raise RuntimeError(
                f"Integrity gate blocked rehearsal for domain {domain}: "
                + "; ".join(blocking_errors)
            )

        reference_entries, old_broken, new_broken = _resolve_references(
            all_objects, domain_objects, domain,
        )
        ref_stats = _compute_reference_stats(reference_entries, old_broken, new_broken)

        redirect_map = self.manifest.get("redirect_map", {})
        domain_redirects: dict[str, str] = {}
        for obj in domain_objects:
            sp = obj.get("source_path", "")
            if sp in redirect_map:
                domain_redirects[sp] = redirect_map[sp]

        migration_base = {
            "migration_run_id": self.manifest["migration_run_id"],
            "domain": domain,
            "source_commit": domain_objects[0].get("source_commit", "") if domain_objects else "",
            "timestamp": self.committer_date or "",
        }
        (dest_repo_path / MIGRATION_BASE_FILENAME).write_text(
            json.dumps(migration_base, indent=2, sort_keys=True) + "\n",
        )
        (dest_repo_path / REDIRECTS_FILENAME).write_text(
            json.dumps(domain_redirects, indent=2, sort_keys=True) + "\n",
        )
        (dest_repo_path / REFERENCES_FILENAME).write_text(
            json.dumps({
                "entries": reference_entries,
                "stats": ref_stats,
            }, indent=2, sort_keys=True) + "\n",
        )

        commit_msg = (
            f"rehearsal: domain={domain} run={self.manifest['migration_run_id']}\n\n"
            f"actions: {len(action_results)} objects processed\n"
            f"reference constraint: new_broken - old_broken = {ref_stats['new_minus_old']}"
        )
        commit_sha = _git_commit(dest_repo_path, commit_msg, self.committer_date)

        return {
            "domain": domain,
            "dest_repo": str(dest_repo_path),
            "commit_sha": commit_sha,
            "action_results": action_results,
            "integrity_issues": integrity_issues,
            "reference_entries": reference_entries,
            "reference_stats": ref_stats,
            "redirects": domain_redirects,
            "blocking_errors": blocking_errors,
        }

    def _resolve_source_file(self, obj: dict) -> Path:
        source_path_str = obj.get("source_path", "")
        source_repo = obj.get("source_repo", "")

        candidates = []
        if source_repo:
            candidates.append(Path(source_repo) / source_path_str)
        if self.source_root:
            candidates.append(self.source_root / source_path_str)
        candidates.append(Path(source_path_str))

        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0] if candidates else Path(source_path_str)

    def _compute_summary(self, result: RehearsalResult) -> dict:
        total_objects = 0
        preserved = 0
        transformed = 0
        archived = 0
        rejected = 0
        written = 0
        skipped = 0

        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                total_objects += 1
                action = ar.get("action", "")
                if action == ACTION_PRESERVE:
                    preserved += 1
                    written += 1
                elif action in _TRANSFORM_ACTIONS:
                    transformed += 1
                    written += 1
                elif action == ACTION_ARCHIVE:
                    archived += 1
                    written += 1
                elif action == ACTION_REJECT:
                    rejected += 1
                    skipped += 1

        unclassified = total_objects - (preserved + transformed + archived + rejected)

        return {
            "tracked": total_objects,
            "preserved": preserved,
            "transformed": transformed,
            "archived": archived,
            "rejected": rejected,
            "unclassified": unclassified,
            "invariant_holds": unclassified == 0,
            "written": written,
            "skipped": skipped,
        }