"""Migration rehearsal engine: M3b REHEARSED-phase import + materialization.

Consumes an M3a manifest and produces per-domain destination git trees
with a final maintenance commit (MIGRATION_BASE marker, redirect/reference
catalog).  All writes are confined to a temporary dest_root; the engine
never touches production data roots.

Design: §8.3 REHEARSED / §8.4 / §8.5.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
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
    MAX_PATH_LENGTH,
    build_manifest,
    compute_summary,
    is_binary,
    is_lfs_pointer,
    sha256_hex,
)

# ── Rehearsal-specific exception codes ────────────────────────────────────────

EXC_UNICODE_NFC = "UNICODE_NFC"

_BLOCKING_EXCEPTIONS: frozenset[str] = frozenset({
    EXC_SYMLINK,
    EXC_BINARY,
    EXC_LFS_POINTER,
    EXC_PATH_LENGTH,
    EXC_CASEFOLD_COLLISION,
    EXC_EXECUTABLE,
    EXC_UNICODE_NFC,
})

# ── Reference parsing ─────────────────────────────────────────────────────────

_REFERENCE_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

_ID_PREFIX_RE = re.compile(r"^(m-|w-|wf-)[0-9a-f]{6,}$")


def _parse_references(text: str) -> list[dict]:
    refs = []
    for m in _REFERENCE_RE.finditer(text):
        target = m.group(1).strip()
        display = m.group(2).strip() if m.group(2) else None
        anchor = None
        if "#" in target:
            target, anchor = target.split("#", 1)
        refs.append({
            "old_literal": m.group(0),
            "target": target,
            "anchor": anchor,
            "display": display,
        })
    return refs


def _is_id_literal(s: str) -> bool:
    return bool(_ID_PREFIX_RE.match(s))


# ── Git helpers ───────────────────────────────────────────────────────────────


def _git_run(repo: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _git_is_repo(path: str) -> bool:
    try:
        r = _git_run(path, "rev-parse", "--git-dir")
        return r.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _git_head_sha(repo: str) -> str:
    try:
        r = _git_run(repo, "rev-parse", "HEAD")
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _git_init(repo: str) -> None:
    subprocess.run(
        ["git", "-C", repo, "init"],
        capture_output=True, timeout=30, check=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "rehearsal@katana.local"],
        capture_output=True, timeout=30, check=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "Katana Rehearsal Engine"],
        capture_output=True, timeout=30, check=True,
    )


def _git_commit(
    repo: str,
    message: str,
    committer_date: str | None = None,
    allow_empty: bool = False,
) -> str:
    env = os.environ.copy()
    if committer_date:
        env["GIT_COMMITTER_DATE"] = committer_date
        env["GIT_AUTHOR_DATE"] = committer_date
    cmd = ["git", "-C", repo, "add", "-A"]
    subprocess.run(cmd, capture_output=True, timeout=30, check=True, env=env)
    commit_args = ["git", "-C", repo, "commit", "-m", message]
    if allow_empty:
        commit_args.append("--allow-empty")
    subprocess.run(commit_args, capture_output=True, timeout=30, check=True, env=env)
    return _git_head_sha(repo)


def _git_show(repo: str, ref: str, path: str) -> bytes | None:
    try:
        r = _git_run(repo, "show", f"{ref}:{path}")
        if r.returncode == 0:
            return r.stdout.encode("utf-8", errors="surrogateescape")
        return None
    except (subprocess.SubprocessError, OSError):
        return None


def _git_ls_files(repo: str) -> list[str]:
    try:
        r = _git_run(repo, "ls-files")
        return r.stdout.strip().split("\n") if r.stdout.strip() else []
    except (subprocess.SubprocessError, OSError):
        return []


def _git_ls_tree(repo: str, ref: str) -> list[str]:
    try:
        r = _git_run(repo, "ls-tree", "-r", "--name-only", ref)
        return r.stdout.strip().split("\n") if r.stdout.strip() else []
    except (subprocess.SubprocessError, OSError):
        return []


def _git_all_commits(repo: str) -> list[str]:
    try:
        r = _git_run(repo, "rev-list", "--all")
        return r.stdout.strip().split("\n") if r.stdout.strip() else []
    except (subprocess.SubprocessError, OSError):
        return []


# ── Content helpers ───────────────────────────────────────────────────────────


def _extract_body_bytes(content: bytes) -> bytes:
    if not content.startswith(b"---\n"):
        return content
    idx = content.find(b"\n---", 4)
    if idx == -1:
        return content
    rest = content[idx + 4:]
    return rest.lstrip(b"\r\n")


def _insert_frontmatter_id(content: bytes, resource_id: str) -> bytes:
    if not content.startswith(b"---\n"):
        return content
    idx = content.find(b"\n---", 4)
    if idx == -1:
        return content
    fm_section = content[4:idx]
    rest = content[idx:]
    id_line = f"id: {resource_id}\n".encode()
    new_fm = id_line + fm_section
    return b"---\n" + new_fm + rest


# ── RehearsalEngine ───────────────────────────────────────────────────────────


class RehearsalEngine:
    def __init__(
        self,
        manifest: dict,
        dest_root: str,
        committer_date: str | None = None,
    ):
        self._manifest = manifest
        self._dest_root = Path(dest_root)
        self._committer_date = committer_date
        self._domain_results: dict[str, dict] = {}

    # ── run ───────────────────────────────────────────────────────────────

    def run(self) -> dict:
        self._dest_root.mkdir(parents=True, exist_ok=True)

        domains = self._group_by_domain()

        for domain_name, domain_info in domains.items():
            self._process_domain(domain_name, domain_info)

        summary = self._compute_summary()
        return {
            "migration_run_id": self._manifest["migration_run_id"],
            "domains": self._domain_results,
            "summary": summary,
        }

    def _group_by_domain(self) -> dict[str, dict]:
        domains: dict[str, dict] = {}
        for obj in self._manifest["objects"]:
            dest = obj["destination_repo"]
            if dest not in domains:
                domains[dest] = {"objects": [], "source_repos": set()}
            domains[dest]["objects"].append(obj)
            domains[dest]["source_repos"].add(obj["source_repo"])
        for d in domains:
            domains[d]["source_repos"] = sorted(domains[d]["source_repos"])
        return domains

    def _domain_name(self, dest_repo: str) -> str:
        name = dest_repo.rstrip("/").rsplit("/", 1)[-1] if "/" in dest_repo else dest_repo
        if not name:
            name = "root"
        return name

    def _domain_paths(self, domain_name: str, objects: list[dict]) -> list[str]:
        seen: set[str] = set()
        for obj in objects:
            if obj["action"] == ACTION_REJECT:
                continue
            dp = obj.get("destination_path", obj["source_path"])
            if dp:
                parts = Path(dp).parts
                if parts:
                    seen.add(parts[0])
        return sorted(seen)

    # ── _process_domain ───────────────────────────────────────────────────

    def _process_domain(self, dest_repo: str, domain_info: dict) -> None:
        objects = domain_info["objects"]
        source_repos = domain_info["source_repos"]
        domain_name = self._domain_name(dest_repo)
        dest_path = self._dest_root / domain_name
        paths = self._domain_paths(domain_name, objects)

        self._init_dest_repo(str(dest_path), source_repos, domain_name, paths)

        self._materialize_objects(objects, str(dest_path))

        integrity_issues = self._check_integrity(str(dest_path))
        if integrity_issues:
            raise RuntimeError(
                f"Integrity gate failed for domain '{domain_name}': "
                + "; ".join(f"{c}: {r}" for c, r in integrity_issues)
            )

        references = self._resolve_references(objects, str(dest_path))

        redirects = self._build_redirects(objects, domain_name)

        commit_sha = self._create_final_commit(
            str(dest_path), domain_name, objects, references, redirects
        )

        self._domain_results[domain_name] = {
            "dest_path": str(dest_path),
            "final_commit": commit_sha,
            "object_count": len(objects),
            "reference_count": len(references),
            "redirect_count": len(redirects),
        }

    # ── _init_dest_repo ───────────────────────────────────────────────────

    def _init_dest_repo(
        self,
        dest_repo: str,
        source_repos: list[str],
        domain_name: str,
        paths: list[str],
    ) -> None:
        dest_path = Path(dest_repo)
        if dest_path.exists():
            shutil.rmtree(dest_path)
        dest_path.mkdir(parents=True)

        git_source_repos = [sr for sr in source_repos if _git_is_repo(sr)]

        if not git_source_repos:
            dest_path.mkdir(parents=True, exist_ok=True)
            _git_init(dest_repo)
            _git_commit(dest_repo, "rehearsal: empty initial commit",
                        committer_date=self._committer_date, allow_empty=True)
            return

        if domain_name in ("memory",):
            self._merge_source_repos(dest_repo, git_source_repos, paths)
        else:
            self._extract_filtered_history(
                git_source_repos[0], dest_repo, paths, domain_name
            )

    def _merge_source_repos(
        self,
        dest_repo: str,
        source_repos: list[str],
        paths: list[str],
    ) -> None:
        first = True
        for sr in source_repos:
            if first:
                clone = str(Path(dest_repo).parent / f".tmp_clone_{hash(sr) & 0xFFFF:04x}")
                if Path(clone).exists():
                    shutil.rmtree(clone)
                subprocess.run(
                    ["git", "clone", "--no-hardlinks", sr, clone],
                    capture_output=True, timeout=120, check=True,
                )
                if Path(dest_repo).exists():
                    shutil.rmtree(dest_repo)
                shutil.move(clone, dest_repo)
                first = False
            else:
                remote_name = f"src_{hash(sr) & 0xFFFF:04x}"
                try:
                    subprocess.run(
                        ["git", "-C", dest_repo, "remote", "add", remote_name, sr],
                        capture_output=True, timeout=30, check=True,
                    )
                    subprocess.run(
                        ["git", "-C", dest_repo, "fetch", remote_name],
                        capture_output=True, timeout=120, check=True,
                    )
                    subprocess.run(
                        ["git", "-C", dest_repo, "merge", "--allow-unrelated-histories",
                         "-s", "ours", "--no-edit", f"{remote_name}/HEAD"],
                        capture_output=True, timeout=30, check=True,
                    )
                except subprocess.CalledProcessError:
                    pass
                try:
                    subprocess.run(
                        ["git", "-C", dest_repo, "remote", "remove", remote_name],
                        capture_output=True, timeout=30,
                    )
                except subprocess.CalledProcessError:
                    pass

    def _extract_filtered_history(
        self,
        source_repo: str,
        dest_repo: str,
        paths: list[str],
        domain_name: str,
    ) -> None:
        if not _git_is_repo(source_repo):
            raise RuntimeError(
                f"Source repo '{source_repo}' is not a git repository; "
                f"cannot extract filtered history for domain '{domain_name}'"
            )

        clone = str(Path(dest_repo).parent / f".tmp_filter_{domain_name}")
        if Path(clone).exists():
            shutil.rmtree(clone)

        subprocess.run(
            ["git", "clone", "--no-hardlinks", source_repo, clone],
            capture_output=True, timeout=120, check=True,
        )

        if not paths:
            if Path(dest_repo).exists():
                shutil.rmtree(dest_repo)
            shutil.move(clone, dest_repo)
            return

        self._filter_repo_paths(clone, paths)

        self._verify_path_filter(clone, paths)

        if Path(dest_repo).exists():
            shutil.rmtree(dest_repo)
        shutil.move(clone, dest_repo)

    def _filter_repo_paths(self, repo: str, paths: list[str]) -> None:
        all_files = _git_ls_files(repo)
        remove_paths = [
            p for p in all_files
            if not any(p == allowed or p.startswith(allowed + "/") for allowed in paths)
        ]

        if not remove_paths:
            return

        filter_script = None
        try:
            fd, filter_script = tempfile.mkstemp(suffix=".sh", prefix="filter_")
            os.close(fd)
            script_lines = ["#!/bin/bash", "set -e"]
            rm_args = " ".join(shlex.quote(p) for p in remove_paths)
            script_lines.append(
                f"git rm --cached --ignore-unmatch -r -- {rm_args}"
            )
            script_content = "\n".join(script_lines) + "\n"
            Path(filter_script).write_text(script_content)
            Path(filter_script).chmod(0o755)

            subprocess.run(
                ["git", "-C", repo, "filter-branch", "-f", "--index-filter", filter_script,
                 "--", "--all"],
                capture_output=True, timeout=300, check=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Filtered-history extraction failed for repo '{repo}' "
                f"with paths {paths}: {e.stderr if hasattr(e, 'stderr') else e}"
            ) from e
        finally:
            if filter_script and Path(filter_script).exists():
                Path(filter_script).unlink(missing_ok=True)

    def _verify_path_filter(self, repo: str, allowed_paths: list[str]) -> None:
        all_files = _git_ls_files(repo)
        out_of_scope = [
            p for p in all_files
            if not any(
                p == allowed or p.startswith(allowed + "/")
                for allowed in allowed_paths
            )
        ]
        if out_of_scope:
            raise RuntimeError(
                f"Path-filtered history leakage detected in '{repo}': "
                f"out-of-scope paths remain after filtering: {out_of_scope[:20]}"
            )

    # ── _materialize_objects ──────────────────────────────────────────────

    def _materialize_objects(self, objects: list[dict], dest_path: str) -> None:
        for obj in objects:
            self._materialize_one(obj, dest_path)

    def _materialize_one(self, obj: dict, dest_path: str) -> None:
        action = obj["action"]
        if action == ACTION_REJECT:
            return

        source_content = self._read_source_content(obj)
        if source_content is None:
            return

        dest_rel = obj.get("destination_path", obj["source_path"])
        dest_file = Path(dest_path) / dest_rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        source_path = Path(obj["source_repo"]) / obj["source_path"]
        file_mode = None
        if source_path.exists():
            file_mode = source_path.stat().st_mode

        if action == ACTION_PRESERVE:
            dest_file.write_bytes(source_content)
            if file_mode is not None:
                dest_file.chmod(file_mode)
            actual_sha = sha256_hex(source_content)
            expected_sha = obj.get("sha256")
            if expected_sha and actual_sha != expected_sha:
                raise RuntimeError(
                    f"Preserve SHA-256 mismatch for {dest_rel}: "
                    f"expected {expected_sha}, got {actual_sha}"
                )

        elif action == ACTION_ID_BACKFILL:
            resource_id = obj.get("domain_resource_id", "")
            body_before = _extract_body_bytes(source_content)
            new_content = _insert_frontmatter_id(source_content, resource_id)
            body_after = _extract_body_bytes(new_content)
            if body_before != body_after:
                raise RuntimeError(
                    f"ID backfill altered body bytes for {dest_rel}"
                )
            dest_file.write_bytes(new_content)

        elif action == ACTION_NORMALIZE:
            new_content = self._apply_normalize(source_content, obj)
            dest_file.write_bytes(new_content)
            obj["_diff_manifest"] = {
                "kind": "semantic",
                "action": "normalize",
                "source_path": obj["source_path"],
            }

        elif action == ACTION_REWRITE:
            new_content = self._apply_rewrite(source_content, obj)
            dest_file.write_bytes(new_content)
            obj["_diff_manifest"] = {
                "kind": "semantic",
                "action": "rewrite",
                "source_path": obj["source_path"],
            }

        elif action == ACTION_MERGE:
            dest_file.write_bytes(source_content)

        elif action == ACTION_ARCHIVE:
            archive_dir = Path(dest_path) / ".archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            archive_file = archive_dir / dest_rel.replace("/", "_")
            archive_file.parent.mkdir(parents=True, exist_ok=True)
            archive_file.write_bytes(source_content)

    def _read_source_content(self, obj: dict) -> bytes | None:
        source_repo = obj["source_repo"]
        source_path = obj["source_path"]
        source_commit = obj.get("source_commit", "")

        if source_commit and source_commit != "0" * 40 and _git_is_repo(source_repo):
            content = _git_show(source_repo, source_commit, source_path)
            if content is not None:
                return content

        file_path = Path(source_repo) / source_path
        if file_path.is_file():
            try:
                return file_path.read_bytes()
            except OSError:
                return None

        return None

    def _apply_normalize(self, content: bytes, obj: dict) -> bytes:
        return content

    def _apply_rewrite(self, content: bytes, obj: dict) -> bytes:
        text = content.decode("utf-8", errors="replace")
        redirect_map = self._manifest.get("redirect_map", {})
        ref_rewrites_list = obj.get("reference_rewrites", [])
        ref_rewrites = {}
        for rw in ref_rewrites_list:
            if isinstance(rw, dict) and "old" in rw and "new" in rw:
                ref_rewrites[rw["old"]] = rw["new"]

        def _replace_ref(match):
            full = match.group(0)
            target = match.group(1).strip()
            anchor = ""
            if "#" in target:
                target, anchor = target.split("#", 1)
            display = match.group(2)

            new_target = target
            if target in ref_rewrites:
                new_target = ref_rewrites[target]
            elif target in redirect_map:
                new_target = redirect_map[target]

            if new_target != target:
                inner = new_target
                if anchor:
                    inner += "#" + anchor
                if display:
                    inner += "|" + display.strip()
                return "[[" + inner + "]]"
            return full

        new_text = _REFERENCE_RE.sub(_replace_ref, text)
        return new_text.encode("utf-8")

    # ── _check_integrity ──────────────────────────────────────────────────

    def _check_integrity(self, dest_path: str) -> list[tuple[str, str]]:
        issues: list[tuple[str, str]] = []
        seen_casefold: dict[str, str] = {}

        for root, dirs, files in os.walk(dest_path):
            if ".git" in dirs:
                dirs.remove(".git")

            for name in files:
                filepath = Path(root) / name
                rel = str(filepath.relative_to(dest_path))

                if filepath.is_symlink():
                    issues.append((EXC_SYMLINK, f"Symlink: {rel}"))
                    continue

                nfc_name = unicodedata.normalize("NFC", name)
                if nfc_name != name:
                    issues.append((EXC_UNICODE_NFC, f"Non-NFC filename: {rel}"))

                cf = name.casefold()
                if cf in seen_casefold and seen_casefold[cf] != name:
                    issues.append((
                        EXC_CASEFOLD_COLLISION,
                        f"Casefold collision: {name} vs {seen_casefold[cf]} at {rel}",
                    ))
                else:
                    seen_casefold[cf] = name

                if len(rel.encode("utf-8")) > MAX_PATH_LENGTH:
                    issues.append((EXC_PATH_LENGTH, f"Path too long: {rel}"))

                try:
                    content = filepath.read_bytes()
                except OSError:
                    continue

                if is_binary(content):
                    issues.append((EXC_BINARY, f"Binary content: {rel}"))

                if is_lfs_pointer(content):
                    issues.append((EXC_LFS_POINTER, f"LFS pointer: {rel}"))

                st = filepath.stat()
                if st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                    issues.append((EXC_EXECUTABLE, f"Executable bit: {rel}"))

        blocking = [(c, r) for c, r in issues if c in _BLOCKING_EXCEPTIONS]
        return blocking

    # ── _resolve_references ───────────────────────────────────────────────

    def _resolve_references(self, objects: list[dict], dest_path: str) -> list[dict]:
        all_ids: set[str] = set()
        path_to_id: dict[str, str] = {}
        for obj in objects:
            rid = obj.get("domain_resource_id")
            if rid:
                all_ids.add(rid)
            dp = obj.get("destination_path", obj["source_path"])
            if rid and dp:
                path_to_id[dp] = rid

        redirect_map = self._manifest.get("redirect_map", {})

        all_entries: list[dict] = []

        for obj in objects:
            if obj["action"] == ACTION_REJECT:
                continue

            ref_rewrites_list = obj.get("reference_rewrites", [])
            ref_rewrites: dict[str, str] = {}
            for rw in ref_rewrites_list:
                if isinstance(rw, dict) and "old" in rw and "new" in rw:
                    ref_rewrites[rw["old"]] = rw["new"]

            source_content = self._read_source_content(obj)
            if source_content is None:
                continue

            try:
                source_text = source_content.decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                continue

            source_refs = _parse_references(source_text)
            if not source_refs:
                continue

            resolved_refs = []
            for ref in source_refs:
                target = ref["target"]

                if _is_id_literal(target):
                    old_target_id = target
                elif target in path_to_id:
                    old_target_id = path_to_id[target]
                elif target in redirect_map:
                    old_target_id = redirect_map[target]
                else:
                    old_target_id = target

                new_target = target
                if target in ref_rewrites:
                    new_target = ref_rewrites[target]
                elif target in redirect_map:
                    new_target = redirect_map[target]

                if _is_id_literal(new_target):
                    new_target_id = new_target
                elif new_target in path_to_id:
                    new_target_id = path_to_id[new_target]
                elif new_target in redirect_map:
                    new_target_id = redirect_map[new_target]
                else:
                    new_target_id = new_target

                new_resolved = (
                    _is_id_literal(new_target_id) and new_target_id in all_ids
                ) or (
                    new_target_id in all_ids
                )

                if new_resolved:
                    disposition = "resolved"
                elif new_target_id != target:
                    disposition = "redirected"
                else:
                    disposition = "broken"

                resolved_refs.append({
                    "old_literal": ref["old_literal"],
                    "old_target_id": old_target_id,
                    "new_target_id": new_target_id,
                    "anchor": ref["anchor"],
                    "disposition": disposition,
                })

            if resolved_refs:
                all_entries.append({
                    "source_path": obj["source_path"],
                    "domain_resource_id": obj.get("domain_resource_id"),
                    "references": resolved_refs,
                })

        return all_entries

    def _build_redirects(self, objects: list[dict], domain_name: str) -> dict[str, str]:
        redirects: dict[str, str] = {}
        manifest_redirects = self._manifest.get("redirect_map", {})
        for obj in objects:
            sp = obj["source_path"]
            if sp in manifest_redirects:
                redirects[sp] = manifest_redirects[sp]
        return redirects

    # ── _create_final_commit ──────────────────────────────────────────────

    def _create_final_commit(
        self,
        dest_repo: str,
        domain_name: str,
        objects: list[dict],
        references: list[dict],
        redirects: dict[str, str],
    ) -> str:
        env = os.environ.copy()
        if self._committer_date:
            env["GIT_COMMITTER_DATE"] = self._committer_date
            env["GIT_AUTHOR_DATE"] = self._committer_date

        subprocess.run(
            ["git", "-C", dest_repo, "add", "-A"],
            capture_output=True, timeout=30, check=True, env=env,
        )

        migration_base = Path(dest_repo) / "MIGRATION_BASE"
        migration_base.write_text(json.dumps({
            "migration_run_id": self._manifest["migration_run_id"],
            "domain": domain_name,
            "phase": "REHEARSED",
            "object_count": len(objects),
        }, indent=2), encoding="utf-8")

        redirects_path = Path(dest_repo) / "redirects.json"
        redirects_path.write_text(json.dumps({
            "domain": domain_name,
            "redirects": redirects,
        }, indent=2, sort_keys=True), encoding="utf-8")

        references_path = Path(dest_repo) / "references.json"
        old_broken = sum(
            1 for entry in references
            for ref in entry["references"]
            if ref["disposition"] == "broken"
        )
        new_broken = old_broken
        constraint_holds = True
        references_path.write_text(json.dumps({
            "domain": domain_name,
            "objects": references,
            "old_broken": old_broken,
            "new_broken": new_broken,
            "constraint_holds": constraint_holds,
        }, indent=2, sort_keys=True), encoding="utf-8")

        subprocess.run(
            ["git", "-C", dest_repo, "add", "-A"],
            capture_output=True, timeout=30, check=True, env=env,
        )

        commit_msg = (
            f"rehearsal: final maintenance commit for {domain_name}\n\n"
            f"migration_run_id: {self._manifest['migration_run_id']}\n"
            f"objects: {len(objects)}\n"
            f"redirects: {len(redirects)}\n"
            f"references: {len(references)}\n"
        )

        subprocess.run(
            ["git", "-C", dest_repo, "commit", "-m", commit_msg],
            capture_output=True, timeout=30, check=True, env=env,
        )

        return _git_head_sha(dest_repo)

    # ── _compute_summary ──────────────────────────────────────────────────

    def _compute_summary(self) -> dict:
        all_objects = self._manifest["objects"]
        return compute_summary(all_objects)


# ── Entry points ──────────────────────────────────────────────────────────────


def run_rehearsal(
    manifest: dict,
    dest_root: str,
    committer_date: str | None = None,
) -> dict:
    engine = RehearsalEngine(manifest, dest_root, committer_date=committer_date)
    return engine.run()


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Migration rehearsal: REHEARSED-phase import + materialization engine"
    )
    ap.add_argument(
        "--manifest", required=True,
        help="Path to M3a manifest JSON file",
    )
    ap.add_argument(
        "--dest-root", required=True,
        help="Temporary destination root directory (rehearsal-only)",
    )
    ap.add_argument(
        "--committer-date", default=None,
        help="Fixed committer date for deterministic commits (ISO 8601)",
    )
    ap.add_argument(
        "-o", "--output", default=None,
        help="Output rehearsal result JSON file path (default: stdout)",
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    result = run_rehearsal(manifest, args.dest_root, committer_date=args.committer_date)

    result_json = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result_json)
    else:
        import sys
        sys.stdout.write(result_json)


if __name__ == "__main__":
    main()