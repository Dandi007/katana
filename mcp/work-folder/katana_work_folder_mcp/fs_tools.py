"""FSTools: Full VFS (fs_*) tool surface for the Work Folder app.

Maps design §5.2 operation set to M1 kernel's GovernedVFS (read/discovery)
and GovernedKernel.mutate (write/transaction), with unified success/error envelopes.

Resource-id-primary: mutations on _brief.md files require/revolve resource_id;
non-brief work-folder artifacts (progress.md, context.md, etc.) are governed
ordinary files with hard invariants (append-only changelog/golden-order,
BROKEN block conservation, resume-guide conservation).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from katana_kernel import (
    CASRejectionError,
    GovernedKernel,
    GovernedVFS,
    head_sha,
)
from katana_kernel.gitops import _restore_tree
from katana_kernel.policy import PolicyViolationError
from katana_kernel.vfs import VFSError
from katana_work_folder_mcp.brief import (
    BRIEF_NAME,
    VALID_STATUS,
    BriefError,
    parse_brief,
    render_brief,
    validate_brief,
)

ID_RE = re.compile(r"wf-[0-9a-f]{6}")

_MAX_FILE_SIZE = 1_000_000

_ERROR_CODES = {
    "REVISION_CONFLICT",
    "BASE_COMMIT_CONFLICT",
    "IDEMPOTENCY_CONFLICT",
    "RESOURCE_REPLACED",
    "REF_MISMATCH",
    "POLICY_VIOLATION",
    "INVALID_CONTENT",
    "INVALID_PATH",
    "CONTENT_TOO_LARGE",
    "RESOURCE_NOT_FOUND",
    "RESOURCE_EXISTS",
    "OPERATION_FAILED",
}

_EXCLUDE_DIRS = {".git", ".katana"}

_CRITICAL_FILES = {"progress.md", "golden-order.md"}

_GOVERNED_FILES = {"context.md", "CLAUDE.md", "AGENTS.md"}


class BatchOpError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _make_error(
    code: str,
    message: str,
    *,
    resource_id: str | None = None,
    virtual_path: str | None = None,
    expected_revision: str | None = None,
    actual_revision: str | None = None,
    current_commit: str | None = None,
    violations: list[str] | None = None,
    retryable: bool = False,
) -> dict:
    err: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if resource_id is not None:
        err["resource_id"] = resource_id
    if virtual_path is not None:
        err["virtual_path"] = virtual_path
    if expected_revision is not None:
        err["expected_revision"] = expected_revision
    if actual_revision is not None:
        err["actual_revision"] = actual_revision
    if current_commit is not None:
        err["current_commit"] = current_commit
    if violations is not None:
        err["violations"] = violations
    return err


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _media_type(path: str) -> str:
    if path.endswith(".md"):
        return "text/markdown"
    return "application/octet-stream"


def _make_success(
    *,
    resource_id: str | None,
    virtual_path: str,
    node_type: str,
    size: int | None,
    content: str | None,
    commit: str,
    entries: list[dict] | None = None,
    capabilities: dict | None = None,
    batch_results: list[dict] | None = None,
    hits: list[str] | None = None,
    matches: list[str] | None = None,
    content_field: str | None = None,
    total_lines: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
    **extra,
) -> dict:
    content_hash_val = _content_hash(content) if content is not None and node_type == "file" else None
    env: dict[str, Any] = {
        "resource_id": resource_id,
        "virtual_path": virtual_path,
        "node_type": node_type,
        "size": size,
        "media_type": _media_type(virtual_path) if node_type == "file" else None,
        "content_hash": content_hash_val,
        "resource_revision": content_hash_val if content_hash_val is not None else commit,
        "content_revision": content_hash_val,
        "commit": commit,
    }
    if content is not None:
        env["content"] = content
    if entries is not None:
        env["entries"] = entries
    if capabilities is not None:
        env["capabilities"] = capabilities
    if batch_results is not None:
        env["batch_results"] = batch_results
    if hits is not None:
        env["hits"] = hits
    if matches is not None:
        env["matches"] = matches
    if content_field is not None:
        env["content_field"] = content_field
    if total_lines is not None:
        env["total_lines"] = total_lines
    if offset is not None:
        env["offset"] = offset
    if limit is not None:
        env["limit"] = limit
    env.update(extra)
    return env


def _brief_id_from_content(content: str) -> str | None:
    try:
        r = parse_brief(content)
        return r["frontmatter"].get("id")
    except BriefError:
        return None


def _is_brief_path(path: str) -> bool:
    return path.endswith(BRIEF_NAME) or path.endswith("/" + BRIEF_NAME)


def _brief_name_from_path(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    return parts[-1]


def _extract_changelog_section(content: str) -> tuple[str, str]:
    idx = content.find("## Changelog")
    if idx == -1:
        return (content, "")
    return (content[:idx], content[idx:])


def _extract_section(content: str, heading: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(heading)}.*$", re.MULTILINE)
    m = pattern.search(content)
    if m is None:
        return None
    return m.group(0)


class FSTools:
    def __init__(self, kernel: GovernedKernel, repo_root: str):
        self._kernel = kernel
        self._binding = kernel.get_binding("work-folder")
        self._repo_root = repo_root
        self._vfs: GovernedVFS = self._binding.vfs

    def _commit(self) -> str:
        return head_sha(self._repo_root) or ""

    def _resolve_path(self, path_or_id: str) -> tuple[str | None, str | None, str | None]:
        if ID_RE.fullmatch(path_or_id):
            return self._resolve_by_id(path_or_id)
        return self._resolve_by_path(path_or_id)

    def _resolve_by_id(self, resource_id: str) -> tuple[str | None, str | None, str | None]:
        briefs = self._scan_briefs()
        for b in briefs:
            if b["id"] == resource_id:
                path = b["path"]
                content = self._vfs.read_text(path)
                return (path, resource_id, content)
        return (None, None, None)

    def _resolve_folder_id_path(self, folder_id: str | None, path: str) -> tuple[str, dict | None]:
        """folder_id 给定时，把 path 解释为 folder 内相对逻辑路径，返回 (repo 相对完整 path, error)。

        双重嵌套 bug 的治本点：agent 用 folder_id + folder 内相对 path（如 "design.md"），
        由 MCP 解析 id→canonical folder path 并拼接，agent 无需推导/感知 _wf_root 物理布局，
        结构上不可能再产生 智元工作/工作记录/智元工作/工作记录/ 这类错位嵌套。
        folder_id=None 时原样返回 path（兼容旧 path-based 用法）。
        """
        if folder_id is None:
            return path, None
        brief_path, _fid, _content = self._resolve_by_id(folder_id)
        if brief_path is None:
            return path, _make_error(
                "RESOURCE_NOT_FOUND", f"folder_id not found: {folder_id}",
                resource_id=folder_id, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )
        folder_dir = brief_path.rsplit("/", 1)[0] if "/" in brief_path else ""
        resolved = f"{folder_dir}/{path}" if folder_dir else path
        return resolved, None

    def _resolve_by_path(self, virtual_path: str) -> tuple[str | None, str | None, str | None]:
        try:
            if not self._vfs.exists(virtual_path) or not self._vfs.is_file(virtual_path):
                return (None, None, None)
            content = self._vfs.read_text(virtual_path)
            rid = _brief_id_from_content(content) if _is_brief_path(virtual_path) else None
            return (virtual_path, rid, content)
        except VFSError:
            return (None, None, None)

    def _existing_ids(self) -> set[str]:
        briefs = self._scan_briefs()
        return {b["id"] for b in briefs}

    def _is_valid_work_folder_dir(self, path_to_brief: str) -> bool:
        parts = path_to_brief.replace("\\", "/").split("/")
        if len(parts) < 2:
            return False
        parent = "/".join(parts[:-1])
        try:
            return self._vfs.exists(parent + "/progress.md") and self._vfs.is_file(parent + "/progress.md")
        except VFSError:
            return False

    def _scan_briefs(self) -> list[dict]:
        briefs = []
        for p in self._vfs.ls("**/" + BRIEF_NAME):
            parts = p.replace("\\", "/").split("/")
            if parts[0] in _EXCLUDE_DIRS:
                continue
            if len(parts) < 2:
                continue
            if not self._is_valid_work_folder_dir(p):
                continue
            try:
                text = self._vfs.read_text(p)
            except Exception:
                continue
            try:
                r = parse_brief(text)
            except BriefError:
                continue
            fm = r["frontmatter"]
            pid = fm.get("id")
            if not pid:
                continue
            out = dict(fm)
            out["path"] = p
            out["goal"] = r["goal"]
            briefs.append(out)
        return briefs

    def _scan_all_files(self) -> list[str]:
        files = []
        for p in self._vfs.ls("**/*.md"):
            parts = p.replace("\\", "/").split("/")
            if parts[0] in _EXCLUDE_DIRS:
                continue
            files.append(p)
        return files

    def _find_by_path(self, virtual_path: str) -> dict | None:
        try:
            if not self._vfs.exists(virtual_path) or not self._vfs.is_file(virtual_path):
                return None
            content = self._vfs.read_text(virtual_path)
            if _is_brief_path(virtual_path):
                if not self._is_valid_work_folder_dir(virtual_path):
                    return None
                r = parse_brief(content)
                fm = r["frontmatter"]
                if not fm.get("id"):
                    return None
                out = dict(fm)
                out["path"] = virtual_path
                out["goal"] = r["goal"]
                return out
            return {"path": virtual_path}
        except (VFSError, BriefError):
            return None

    def _call_mutate(self, op: str, args: dict, write_fn, expected_base_sha: str | None,
                     commit_msg: str) -> dict:
        return self._kernel.mutate(
            "work-folder", op, args,
            expected_base_sha=expected_base_sha,
            write_fn=write_fn,
            commit_msg=commit_msg,
        )

    def _check_content_size(self, content: str, path: str) -> dict | None:
        if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
            return _make_error(
                "CONTENT_TOO_LARGE",
                f"content exceeds max file size of {_MAX_FILE_SIZE} bytes",
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )
        return None

    def _check_resource_replaced(self, path: str, resource_id: str | None) -> dict | None:
        if resource_id and self._binding.ledger.is_tombstoned(resource_id):
            return _make_error(
                "RESOURCE_REPLACED",
                f"resource was deleted and replaced: {resource_id}",
                resource_id=resource_id, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )
        existing = self._find_by_path(path)
        if existing and resource_id and existing.get("id") != resource_id:
            return _make_error(
                "RESOURCE_REPLACED",
                f"path {path} now maps to a different resource ({existing.get('id')})",
                resource_id=resource_id, virtual_path=path,
                actual_revision=existing.get("id"),
                current_commit=self._commit(), retryable=False,
            )
        if resource_id is None and _is_brief_path(path):
            manifests = self._binding.manifest.list_manifests()
            for m in manifests:
                op = m.get("op", "")
                if op in ("delete", "fs_delete", "fs_batch") and path in m.get("changed_paths", []):
                    m_result = m.get("result", {})
                    deleted_id = m_result.get("id")
                    if not deleted_id and "batch_results" in m_result:
                        for br in m_result["batch_results"]:
                            if br.get("op") in ("fs_delete",) and br.get("path") == path:
                                deleted_id = br.get("resource_id")
                                break
                    if deleted_id and self._binding.ledger.is_tombstoned(deleted_id):
                        return _make_error(
                            "RESOURCE_REPLACED",
                            f"path {path} was previously deleted and tombstoned",
                            resource_id=deleted_id, virtual_path=path,
                            current_commit=self._commit(), retryable=False,
                        )
        return None

    def _check_idempotency(self, idempotency_key: str | None) -> dict | None:
        if idempotency_key is None:
            return None
        manifests = self._binding.manifest.list_manifests()
        for m in manifests:
            result = m.get("result", {})
            if result.get("idempotency_key") == idempotency_key:
                return _make_error(
                    "IDEMPOTENCY_CONFLICT",
                    f"idempotency key already used: {idempotency_key}",
                    current_commit=self._commit(), retryable=False,
                )
        return None

    def _check_revision(self, path: str, expected_resource_revision: str | None) -> dict | None:
        if expected_resource_revision is None:
            return None
        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return None
            content = self._vfs.read_text(path)
        except VFSError:
            return None
        actual = _content_hash(content)
        if actual != expected_resource_revision:
            return _make_error(
                "REVISION_CONFLICT",
                "resource has been modified since expected revision",
                virtual_path=path,
                expected_revision=expected_resource_revision,
                actual_revision=actual,
                current_commit=self._commit(), retryable=True,
            )
        return None

    def _check_path(self, path: str) -> str | None:
        if ".." in path or path.startswith("/"):
            return ("path must not contain '..' or absolute paths; "
                    "prefer fs_<op>(folder_id='wf-xxxxxx', path='<folder-relative>') "
                    "to avoid deriving physical layout (root cause of double-nesting)")
        if path.startswith("."):
            return "path must not start with '.'"
        parts = path.replace("\\", "/").split("/")
        if parts[0] in _EXCLUDE_DIRS:
            return f"path must not be inside excluded directory: {parts[0]}"
        return None

    def _check_changelog_append_only(self, old_content: str, new_content: str) -> bool:
        _, old_changelog = _extract_changelog_section(old_content)
        _, new_changelog = _extract_changelog_section(new_content)
        if not old_changelog:
            return True
        return new_changelog.startswith(old_changelog)

    def _check_append_only(self, old_content: str, new_content: str) -> bool:
        return new_content.startswith(old_content)

    def _check_resume_guide_conserved(self, old_content: str, new_content: str) -> bool:
        required_sections = [
            "## Goal",
            "## Status",
            "## Key Context",
            "## Resume Steps",
        ]
        for section in required_sections:
            if _extract_section(old_content, section) is not None:
                if _extract_section(new_content, section) is None:
                    return False
        return True

    def _check_context_conserved(self, old_content: str, new_content: str) -> bool:
        required_sections = [
            "## 工作上下文",
            "## 关键路径",
            "## 环境信息",
        ]
        for section in required_sections:
            if _extract_section(old_content, section) is not None:
                if _extract_section(new_content, section) is None:
                    return False
        return True

    def _check_broken_blocks_conserved(self, old_content: str, new_content: str) -> bool:
        old_blocked = _extract_section(old_content, "## Blocked")
        if old_blocked is None:
            return True
        new_blocked = _extract_section(new_content, "## Blocked")
        if new_blocked is None:
            return False
        return True

    def _check_hard_invariants(self, path: str, op: str,
                                old_content: str | None = None,
                                new_content: str | None = None) -> dict | None:
        fname = _brief_name_from_path(path)

        if fname in _CRITICAL_FILES:
            if op in ("fs_delete",):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"cannot delete critical file {fname}; use domain tools",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )

        if _is_brief_path(path):
            try:
                if self._vfs.exists(path) and self._vfs.is_file(path):
                    content = self._vfs.read_text(path)
                    r = parse_brief(content)
                    status = r["frontmatter"].get("status", "")
                    if status == "completed" and op in ("fs_write", "fs_edit", "fs_delete"):
                        return _make_error(
                            "POLICY_VIOLATION",
                            f"cannot mutate completed work folder: {path}",
                            virtual_path=path, current_commit=self._commit(), retryable=False,
                        )
            except (VFSError, BriefError):
                pass

        return None

    def _check_governed_invariants(self, path: str, op: str,
                                    old_content: str | None,
                                    new_content: str | None) -> dict | None:
        fname = _brief_name_from_path(path)
        if old_content is None or new_content is None:
            return None

        if fname == "progress.md":
            if not self._check_changelog_append_only(old_content, new_content):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"changelog section of {fname} is append-only; rewrite/reorder rejected",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )
            if not self._check_broken_blocks_conserved(old_content, new_content):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"BROKEN blocks in {fname} must be conserved",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )
        elif fname == "golden-order.md":
            if not self._check_append_only(old_content, new_content):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"{fname} is append-only; rewrite/truncation rejected",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )
        elif fname in ("CLAUDE.md", "AGENTS.md"):
            if not self._check_resume_guide_conserved(old_content, new_content):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"resume guide in {fname} must be conserved; do not remove ## Goal, ## Status, ## Key Context, ## Resume Steps sections",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )
        elif fname == "context.md":
            if not self._check_context_conserved(old_content, new_content):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"context structure in {fname} must be conserved; do not remove ## 工作上下文, ## 关键路径, ## 环境信息 sections",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )

        return None

    def _validate_brief_content(self, content: str, allow_missing_id: bool = False) -> str | None:
        try:
            r = parse_brief(content)
        except BriefError as e:
            return f"content is not a valid brief: {e}"
        fm = r["frontmatter"]
        if not allow_missing_id and not fm.get("id"):
            return "brief must have an id"
        if fm.get("id") and not ID_RE.fullmatch(fm["id"]):
            return f"invalid id format: {fm['id']}"
        if not fm.get("title"):
            return "brief must have a title"
        if not fm.get("status"):
            return "brief must have a status"
        if fm.get("status") and fm["status"] not in VALID_STATUS:
            return f"invalid status: {fm['status']} (must be one of {sorted(VALID_STATUS)})"
        if not fm.get("created"):
            return "brief must have created date"
        if not fm.get("updated"):
            return "brief must have updated date"
        problems = validate_brief(content)
        if allow_missing_id:
            problems = [p for p in problems if "缺少 frontmatter 字段: id" not in p]
        if problems:
            return "; ".join(problems)
        return None

    def _generate_new_id(self) -> str:
        return self._binding.ledger.gen_id(self._existing_ids())

    def _inject_id(self, content: str, new_id: str) -> str:
        try:
            r = parse_brief(content)
        except BriefError:
            return content
        fm = r["frontmatter"]
        fm["id"] = new_id
        return render_brief(
            id=new_id,
            title=fm.get("title", ""),
            status=fm.get("status", "active"),
            created=fm.get("created", ""),
            updated=fm.get("updated", ""),
            goal=r["goal"],
            summary=r["summary"],
            tags=fm.get("tags") or (),
            kind=fm.get("kind") or "",
            links=fm.get("links") or (),
        )

    def _check_duplicate_id(self, new_rid: str) -> bool:
        return new_rid in self._existing_ids()

    def _validate_resource_id_for_path(self, path: str, resource_id: str | None) -> dict | None:
        if resource_id is None:
            return None
        if self._binding.ledger.is_tombstoned(resource_id):
            return _make_error(
                "RESOURCE_REPLACED",
                f"resource was deleted: {resource_id}",
                resource_id=resource_id, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )
        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"resource not found at path: {path}",
                    resource_id=resource_id, virtual_path=path,
                    current_commit=self._commit(), retryable=False,
                )
            content = self._vfs.read_text(path)
            rid = _brief_id_from_content(content) if _is_brief_path(path) else None
            if rid != resource_id:
                return _make_error(
                    "REF_MISMATCH",
                    f"resource_id {resource_id} does not match path {path} (found {rid})",
                    resource_id=resource_id, virtual_path=path,
                    current_commit=self._commit(), retryable=False,
                )
        except VFSError:
            return _make_error(
                "RESOURCE_NOT_FOUND",
                f"resource not found at path: {path}",
                resource_id=resource_id, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )
        return None

    def _resolve_effective_resource_id(self, path: str, resource_id: str | None) -> tuple[str | None, dict | None]:
        if not _is_brief_path(path):
            return (None, None)
        if resource_id is not None:
            err = self._validate_resource_id_for_path(path, resource_id)
            if err:
                return (None, err)
            return (resource_id, None)
        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return (None, _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"resource not found at path: {path}",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                ))
            content = self._vfs.read_text(path)
            rid = _brief_id_from_content(content)
            if rid is None:
                return (None, _make_error(
                    "INVALID_CONTENT",
                    f"file at {path} has no valid resource id",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                ))
            if self._binding.ledger.is_tombstoned(rid):
                return (None, _make_error(
                    "RESOURCE_REPLACED",
                    f"resource was deleted: {rid}",
                    resource_id=rid, virtual_path=path,
                    current_commit=self._commit(), retryable=False,
                ))
            return (rid, None)
        except VFSError:
            return (None, _make_error(
                "RESOURCE_NOT_FOUND",
                f"resource not found at path: {path}",
                virtual_path=path, current_commit=self._commit(), retryable=False,
            ))

    def _preflight_batch_op(self, op_spec: dict, index: int) -> dict | None:
        op_name = op_spec.get("op", "")
        op_args = op_spec.get("args", {})
        commit = self._commit()

        valid_ops = {"fs_create", "fs_write", "fs_edit", "fs_copy", "fs_rename", "fs_delete"}
        if op_name not in valid_ops:
            return _make_error(
                "INVALID_CONTENT",
                f"batch op {index}: unknown operation: {op_name}",
                current_commit=commit, retryable=False,
            )

        paths_to_check: list[tuple[str, str]] = []

        if op_name == "fs_create":
            path = op_args.get("path", "")
            paths_to_check.append((path, "path"))
        elif op_name == "fs_write":
            path = op_args.get("path", "")
            paths_to_check.append((path, "path"))
        elif op_name == "fs_edit":
            path = op_args.get("path", "")
            paths_to_check.append((path, "path"))
        elif op_name == "fs_copy":
            source = op_args.get("source", "")
            dest = op_args.get("dest", "")
            paths_to_check.append((source, "source"))
            paths_to_check.append((dest, "dest"))
        elif op_name == "fs_rename":
            source = op_args.get("source", "")
            dest = op_args.get("dest", "")
            paths_to_check.append((source, "source"))
            paths_to_check.append((dest, "dest"))
        elif op_name == "fs_delete":
            path = op_args.get("path", "")
            paths_to_check.append((path, "path"))

        for p, label in paths_to_check:
            p_err = self._check_path(p)
            if p_err:
                return _make_error(
                    "INVALID_PATH",
                    f"batch op {index}: {label} {p_err}",
                    virtual_path=p, current_commit=commit, retryable=False,
                )

        if op_name == "fs_create":
            path = op_args.get("path", "")
            content = op_args.get("content", "")
            if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
                return _make_error(
                    "CONTENT_TOO_LARGE",
                    f"batch op {index}: content exceeds max file size",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            try:
                if self._vfs.exists(path):
                    return _make_error(
                        "RESOURCE_EXISTS",
                        f"batch op {index}: path already exists: {path}",
                        virtual_path=path, current_commit=commit, retryable=False,
                    )
            except VFSError as e:
                return _make_error(
                    "INVALID_PATH", str(e),
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            resource_id = op_args.get("resource_id")
            if resource_id is not None:
                if self._binding.ledger.is_tombstoned(resource_id):
                    return _make_error(
                        "RESOURCE_REPLACED",
                        f"batch op {index}: resource was deleted: {resource_id}",
                        resource_id=resource_id, virtual_path=path,
                        current_commit=commit, retryable=False,
                    )
            if _is_brief_path(path):
                if not self._is_valid_work_folder_dir(path):
                    return _make_error(
                        "POLICY_VIOLATION",
                        f"batch op {index}: cannot create _brief.md in non-work-folder directory; use domain tools",
                        virtual_path=path, current_commit=commit, retryable=False,
                    )
            else:
                fname = _brief_name_from_path(path)
                if fname in _CRITICAL_FILES:
                    return _make_error(
                        "POLICY_VIOLATION",
                        f"batch op {index}: cannot create critical file {fname} via fs_create; use domain tools",
                        virtual_path=path, current_commit=commit, retryable=False,
                    )

        elif op_name == "fs_write":
            path = op_args.get("path", "")
            content = op_args.get("content", "")
            if len(content.encode("utf-8")) > _MAX_FILE_SIZE:
                return _make_error(
                    "CONTENT_TOO_LARGE",
                    f"batch op {index}: content exceeds max file size",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            resource_id = op_args.get("resource_id")
            if resource_id is not None:
                if self._binding.ledger.is_tombstoned(resource_id):
                    return _make_error(
                        "RESOURCE_REPLACED",
                        f"batch op {index}: resource was deleted: {resource_id}",
                        resource_id=resource_id, virtual_path=path,
                        current_commit=commit, retryable=False,
                    )
            try:
                if not self._vfs.exists(path) or not self._vfs.is_file(path):
                    return _make_error(
                        "RESOURCE_NOT_FOUND",
                        f"batch op {index}: file not found (write does not implicitly create): {path}",
                        virtual_path=path, current_commit=commit, retryable=False,
                    )
            except VFSError as e:
                return _make_error(
                    "INVALID_PATH", str(e),
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            if resource_id is not None:
                try:
                    old_content = self._vfs.read_text(path)
                    old_rid = _brief_id_from_content(old_content) if _is_brief_path(path) else None
                    if old_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match path {path} (found {old_rid})",
                            resource_id=resource_id, virtual_path=path,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass
            expected_rev = op_args.get("expected_resource_revision")
            if expected_rev is not None:
                try:
                    old_content = self._vfs.read_text(path)
                    actual = _content_hash(old_content)
                    if actual != expected_rev:
                        return _make_error(
                            "REVISION_CONFLICT",
                            f"batch op {index}: resource has been modified since expected revision",
                            virtual_path=path,
                            expected_revision=expected_rev,
                            actual_revision=actual,
                            current_commit=commit, retryable=True,
                        )
                except VFSError:
                    pass
            if _is_brief_path(path):
                try:
                    if self._vfs.exists(path) and self._vfs.is_file(path):
                        old_content = self._vfs.read_text(path)
                        r = parse_brief(old_content)
                        status = r["frontmatter"].get("status", "")
                        if status == "completed":
                            return _make_error(
                                "POLICY_VIOLATION",
                                f"batch op {index}: cannot mutate completed work folder: {path}",
                                virtual_path=path, current_commit=commit, retryable=False,
                            )
                except (VFSError, BriefError):
                    pass

        elif op_name == "fs_edit":
            path = op_args.get("path", "")
            resource_id = op_args.get("resource_id")
            if resource_id is not None:
                if self._binding.ledger.is_tombstoned(resource_id):
                    return _make_error(
                        "RESOURCE_REPLACED",
                        f"batch op {index}: resource was deleted: {resource_id}",
                        resource_id=resource_id, virtual_path=path,
                        current_commit=commit, retryable=False,
                    )
            try:
                if not self._vfs.exists(path) or not self._vfs.is_file(path):
                    return _make_error(
                        "RESOURCE_NOT_FOUND",
                        f"batch op {index}: file not found: {path}",
                        virtual_path=path, current_commit=commit, retryable=False,
                    )
            except VFSError as e:
                return _make_error(
                    "INVALID_PATH", str(e),
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            if resource_id is not None:
                try:
                    old_content = self._vfs.read_text(path)
                    old_rid = _brief_id_from_content(old_content) if _is_brief_path(path) else None
                    if old_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match path {path} (found {old_rid})",
                            resource_id=resource_id, virtual_path=path,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass
            expected_rev = op_args.get("expected_resource_revision")
            if expected_rev is not None:
                try:
                    old_content = self._vfs.read_text(path)
                    actual = _content_hash(old_content)
                    if actual != expected_rev:
                        return _make_error(
                            "REVISION_CONFLICT",
                            f"batch op {index}: resource has been modified since expected revision",
                            virtual_path=path,
                            expected_revision=expected_rev,
                            actual_revision=actual,
                            current_commit=commit, retryable=True,
                        )
                except VFSError:
                    pass
            if _is_brief_path(path):
                try:
                    if self._vfs.exists(path) and self._vfs.is_file(path):
                        old_content = self._vfs.read_text(path)
                        r = parse_brief(old_content)
                        status = r["frontmatter"].get("status", "")
                        if status == "completed":
                            return _make_error(
                                "POLICY_VIOLATION",
                                f"batch op {index}: cannot mutate completed work folder: {path}",
                                virtual_path=path, current_commit=commit, retryable=False,
                            )
                except (VFSError, BriefError):
                    pass

        elif op_name == "fs_copy":
            source = op_args.get("source", "")
            dest = op_args.get("dest", "")
            resource_id = op_args.get("resource_id")
            if resource_id is not None:
                if self._binding.ledger.is_tombstoned(resource_id):
                    return _make_error(
                        "RESOURCE_REPLACED",
                        f"batch op {index}: source resource was deleted: {resource_id}",
                        resource_id=resource_id, virtual_path=source,
                        current_commit=commit, retryable=False,
                    )
            try:
                if not self._vfs.exists(source) or not self._vfs.is_file(source):
                    return _make_error(
                        "RESOURCE_NOT_FOUND",
                        f"batch op {index}: source not found: {source}",
                        virtual_path=source, current_commit=commit, retryable=False,
                    )
                if self._vfs.exists(dest):
                    return _make_error(
                        "RESOURCE_EXISTS",
                        f"batch op {index}: destination already exists: {dest}",
                        virtual_path=dest, current_commit=commit, retryable=False,
                    )
            except VFSError as e:
                return _make_error(
                    "INVALID_PATH", str(e),
                    virtual_path=source, current_commit=commit, retryable=False,
                )
            for p in (source, dest):
                if _brief_name_from_path(p) in _CRITICAL_FILES:
                    return _make_error(
                        "POLICY_VIOLATION",
                        f"batch op {index}: cannot copy critical file {_brief_name_from_path(p)}; use domain tools",
                        virtual_path=p, current_commit=commit, retryable=False,
                    )
            source_is_brief = _is_brief_path(source)
            dest_is_brief = _is_brief_path(dest)
            if not source_is_brief and dest_is_brief:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot create a _brief.md identity from a non-brief file",
                    virtual_path=dest, current_commit=commit, retryable=False,
                )
            if source_is_brief and not dest_is_brief:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot copy a _brief.md resource to a non-brief path",
                    virtual_path=source, current_commit=commit, retryable=False,
                )
            if dest_is_brief and not self._is_valid_work_folder_dir(dest):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot create _brief.md in non-work-folder directory",
                    virtual_path=dest, current_commit=commit, retryable=False,
                )
            if resource_id is not None:
                try:
                    src_content = self._vfs.read_text(source)
                    src_rid = _brief_id_from_content(src_content) if _is_brief_path(source) else None
                    if src_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match source {source} (found {src_rid})",
                            resource_id=resource_id, virtual_path=source,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass

        elif op_name == "fs_rename":
            source = op_args.get("source", "")
            dest = op_args.get("dest", "")
            resource_id = op_args.get("resource_id")
            if resource_id is not None:
                if self._binding.ledger.is_tombstoned(resource_id):
                    return _make_error(
                        "RESOURCE_REPLACED",
                        f"batch op {index}: source resource was deleted: {resource_id}",
                        resource_id=resource_id, virtual_path=source,
                        current_commit=commit, retryable=False,
                    )
            try:
                if not self._vfs.exists(source) or not self._vfs.is_file(source):
                    return _make_error(
                        "RESOURCE_NOT_FOUND",
                        f"batch op {index}: source not found: {source}",
                        virtual_path=source, current_commit=commit, retryable=False,
                    )
                if self._vfs.exists(dest):
                    return _make_error(
                        "RESOURCE_EXISTS",
                        f"batch op {index}: destination already exists: {dest}",
                        virtual_path=dest, current_commit=commit, retryable=False,
                    )
            except VFSError as e:
                return _make_error(
                    "INVALID_PATH", str(e),
                    virtual_path=source, current_commit=commit, retryable=False,
                )
            for p in (source, dest):
                if _brief_name_from_path(p) in _CRITICAL_FILES:
                    return _make_error(
                        "POLICY_VIOLATION",
                        f"batch op {index}: cannot rename critical file {_brief_name_from_path(p)}; use domain tools",
                        virtual_path=p, current_commit=commit, retryable=False,
                    )
            source_is_brief = _is_brief_path(source)
            dest_is_brief = _is_brief_path(dest)
            if not source_is_brief and dest_is_brief:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot create a _brief.md identity from a non-brief file",
                    virtual_path=dest, current_commit=commit, retryable=False,
                )
            if source_is_brief and not dest_is_brief:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot move a _brief.md resource to a non-brief destination",
                    virtual_path=source, current_commit=commit, retryable=False,
                )
            if dest_is_brief and not self._is_valid_work_folder_dir(dest):
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot rename _brief.md into non-work-folder directory",
                    virtual_path=dest, current_commit=commit, retryable=False,
                )
            if resource_id is not None:
                try:
                    src_content = self._vfs.read_text(source)
                    src_rid = _brief_id_from_content(src_content) if _is_brief_path(source) else None
                    if src_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match source {source} (found {src_rid})",
                            resource_id=resource_id, virtual_path=source,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass

        elif op_name == "fs_delete":
            path = op_args.get("path", "")
            resource_id = op_args.get("resource_id")
            if resource_id is not None:
                if self._binding.ledger.is_tombstoned(resource_id):
                    return _make_error(
                        "RESOURCE_REPLACED",
                        f"batch op {index}: resource was deleted: {resource_id}",
                        resource_id=resource_id, virtual_path=path,
                        current_commit=commit, retryable=False,
                    )
            try:
                if not self._vfs.exists(path) or not self._vfs.is_file(path):
                    return _make_error(
                        "RESOURCE_NOT_FOUND",
                        f"batch op {index}: file not found: {path}",
                        virtual_path=path, current_commit=commit, retryable=False,
                    )
            except VFSError as e:
                return _make_error(
                    "INVALID_PATH", str(e),
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            fname = _brief_name_from_path(path)
            if fname in _CRITICAL_FILES:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"batch op {index}: cannot delete critical file {fname}; use domain tools",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            if resource_id is not None:
                try:
                    content = self._vfs.read_text(path)
                    rid = _brief_id_from_content(content) if _is_brief_path(path) else None
                    if rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match path {path} (found {rid})",
                            resource_id=resource_id, virtual_path=path,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass

            if _is_brief_path(path):
                try:
                    if self._vfs.exists(path) and self._vfs.is_file(path):
                        content = self._vfs.read_text(path)
                        r = parse_brief(content)
                        status = r["frontmatter"].get("status", "")
                        if status == "completed":
                            return _make_error(
                                "POLICY_VIOLATION",
                                f"batch op {index}: cannot mutate completed work folder: {path}",
                                virtual_path=path, current_commit=commit, retryable=False,
                            )
                except (VFSError, BriefError):
                    pass

        return None

    # ── fs_capabilities ──────────────────────────────────────────────────────

    def fs_capabilities(self) -> dict:
        commit = self._commit()
        return _make_success(
            resource_id=None,
            virtual_path="",
            node_type="capabilities",
            size=None,
            content=None,
            commit=commit,
            capabilities={
                "operations": [
                    "fs_capabilities", "fs_resolve", "fs_stat", "fs_list",
                    "fs_glob", "fs_read", "fs_create", "fs_write", "fs_edit",
                    "fs_copy", "fs_rename", "fs_delete", "fs_batch",
                ],
                "max_file_size": _MAX_FILE_SIZE,
                "supported_media_types": ["text/markdown"],
                "idempotency": "idempotency_key + expected_base_commit + expected_resource_revision",
            },
        )

    # ── fs_resolve ───────────────────────────────────────────────────────────

    def fs_resolve(self, path_or_id: str) -> dict:
        commit = self._commit()
        if self._binding.ledger.is_tombstoned(path_or_id):
            return _make_error(
                "RESOURCE_REPLACED",
                f"resource was deleted: {path_or_id}",
                resource_id=path_or_id,
                current_commit=commit,
                retryable=False,
            )
        if not ID_RE.fullmatch(path_or_id):
            p_err = self._check_path(path_or_id)
            if p_err:
                return _make_error(
                    "INVALID_PATH", p_err,
                    virtual_path=path_or_id, current_commit=commit, retryable=False,
                )
        virtual_path, resource_id, content = self._resolve_path(path_or_id)
        if virtual_path is None:
            return _make_error(
                "RESOURCE_NOT_FOUND",
                f"resource not found: {path_or_id}",
                virtual_path=path_or_id,
                resource_id=path_or_id if ID_RE.fullmatch(path_or_id) else None,
                current_commit=commit,
                retryable=False,
            )
        return _make_success(
            resource_id=resource_id,
            virtual_path=virtual_path,
            node_type="file",
            size=len(content.encode("utf-8")),
            content=content,
            commit=commit,
        )

    # ── fs_stat ──────────────────────────────────────────────────────────────

    def fs_stat(self, path: str) -> dict:
        commit = self._commit()
        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )
        try:
            st = self._vfs.stat(path)
        except VFSError as e:
            if "not found" in str(e).lower() or "no such file" in str(e).lower():
                return _make_error(
                    "RESOURCE_NOT_FOUND", str(e),
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=commit, retryable=False,
            )

        if st["is_dir"]:
            parts = path.replace("\\", "/").split("/")
            if parts[0] in _EXCLUDE_DIRS:
                return _make_error(
                    "INVALID_PATH",
                    f"stat on excluded directory: {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            return _make_success(
                resource_id=None,
                virtual_path=path.rstrip("/") + "/",
                node_type="directory",
                size=None,
                content=None,
                commit=commit,
            )

        if st["is_file"]:
            content = self._vfs.read_text(path)
            rid = _brief_id_from_content(content) if _is_brief_path(path) and self._is_valid_work_folder_dir(path) else None
            return _make_success(
                resource_id=rid,
                virtual_path=path,
                node_type="file",
                size=st["size"],
                content=content,
                commit=commit,
            )

        return _make_error(
            "RESOURCE_NOT_FOUND",
            f"path not found: {path}",
            virtual_path=path, current_commit=commit, retryable=False,
        )

    # ── fs_list ──────────────────────────────────────────────────────────────

    def fs_list(self, path: str = "") -> dict:
        commit = self._commit()
        if path:
            p_err = self._check_path(path)
            if p_err:
                return _make_error(
                    "INVALID_PATH", p_err,
                    virtual_path=path, current_commit=commit, retryable=False,
                )
        try:
            if path and self._vfs.exists(path) and self._vfs.is_dir(path):
                entries = []
                for p in self._vfs.ls(f"{path}/*.md"):
                    entries.append(self._make_entry(p, commit))
                return _make_success(
                    resource_id=None,
                    virtual_path=path.rstrip("/") + "/",
                    node_type="directory",
                    size=None,
                    content=None,
                    commit=commit,
                    entries=entries,
                )
            elif not path:
                entries = []
                for p in self._vfs.ls("**/*.md"):
                    parts = p.replace("\\", "/").split("/")
                    if parts[0] in _EXCLUDE_DIRS:
                        continue
                    entries.append(self._make_entry(p, commit))
                return _make_success(
                    resource_id=None,
                    virtual_path="",
                    node_type="directory",
                    size=None,
                    content=None,
                    commit=commit,
                    entries=entries,
                )
            else:
                return _make_error(
                    "INVALID_PATH",
                    f"not a directory: {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=commit, retryable=False,
            )

    def _make_entry(self, virtual_path: str, commit: str) -> dict:
        try:
            content = self._vfs.read_text(virtual_path)
            st = self._vfs.stat(virtual_path)
            rid = _brief_id_from_content(content) if _is_brief_path(virtual_path) and self._is_valid_work_folder_dir(virtual_path) else None
            ch = _content_hash(content)
            return {
                "resource_id": rid,
                "virtual_path": virtual_path,
                "node_type": "file",
                "size": st["size"],
                "media_type": _media_type(virtual_path),
                "content_hash": ch,
                "resource_revision": ch if ch is not None else commit,
                "content_revision": ch,
            }
        except Exception:
            return {
                "resource_id": None,
                "virtual_path": virtual_path,
                "node_type": "file",
                "size": 0,
                "media_type": _media_type(virtual_path),
                "content_hash": None,
                "resource_revision": commit,
                "content_revision": None,
            }

    # ── fs_glob ──────────────────────────────────────────────────────────────

    def fs_glob(self, pattern: str) -> dict:
        commit = self._commit()
        if ".." in pattern or pattern.startswith("/"):
            return _make_error(
                "INVALID_PATH", "glob pattern must not contain '..' or absolute paths",
                virtual_path=pattern, current_commit=commit, retryable=False,
            )
        parts = pattern.replace("\\", "/").split("/")
        if parts[0] in _EXCLUDE_DIRS:
            return _make_error(
                "INVALID_PATH",
                f"glob pattern must not target excluded directory: {parts[0]}",
                virtual_path=pattern, current_commit=commit, retryable=False,
            )
        try:
            hits = self._vfs.ls(pattern)
        except (VFSError, ValueError) as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=pattern, current_commit=commit, retryable=False,
            )
        entries = [self._make_entry(p, commit) for p in hits]
        return _make_success(
            resource_id=None,
            virtual_path=pattern,
            node_type="glob",
            size=None,
            content=None,
            commit=commit,
            hits=hits,
            entries=entries,
        )

    # ── fs_read ──────────────────────────────────────────────────────────────

    def fs_read(self, path: str, offset: int | None = None, limit: int | None = None) -> dict:
        commit = self._commit()
        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )
        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"file not found: {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            content = self._vfs.read_text(path)
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=commit, retryable=False,
            )

        lines = content.split("\n")
        total = len(lines)
        start = max(1, offset or 1)
        last = min(total, start + limit - 1) if limit is not None else total
        if start > total or start > last:
            rendered = ""
        else:
            rendered = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(start, last + 1))

        rid = _brief_id_from_content(content) if _is_brief_path(path) and self._is_valid_work_folder_dir(path) else None
        st = self._vfs.stat(path)
        return _make_success(
            resource_id=rid,
            virtual_path=path,
            node_type="file",
            size=st["size"],
            content=content,
            commit=commit,
            content_field=rendered,
            total_lines=total,
            offset=start,
            limit=limit,
        )

    # ── fs_create ────────────────────────────────────────────────────────────

    def fs_create(self, path: str, content: str,
                  folder_id: str | None = None,
                  resource_id: str | None = None,
                  expected_base_commit: str | None = None,
                  idempotency_key: str | None = None) -> dict:
        path, _fid_err = self._resolve_folder_id_path(folder_id, path)
        if _fid_err:
            return _fid_err
        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )

        if resource_id is not None:
            if self._binding.ledger.is_tombstoned(resource_id):
                return _make_error(
                    "RESOURCE_REPLACED",
                    f"resource was deleted: {resource_id}",
                    resource_id=resource_id, virtual_path=path,
                    current_commit=self._commit(), retryable=False,
                )
            if self._check_duplicate_id(resource_id):
                return _make_error(
                    "REF_MISMATCH",
                    f"id {resource_id} is already in use by another resource",
                    resource_id=resource_id, virtual_path=path,
                    current_commit=self._commit(), retryable=False,
                )

        size_err = self._check_content_size(content, path)
        if size_err:
            return size_err

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        try:
            if self._vfs.exists(path):
                commit = self._commit()
                return _make_error(
                    "RESOURCE_EXISTS",
                    f"path already exists: {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )

        if _is_brief_path(path):
            err = self._validate_brief_content(content, allow_missing_id=True)
            if err:
                return _make_error(
                    "INVALID_CONTENT", err,
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )
            if not self._is_valid_work_folder_dir(path):
                return _make_error(
                    "POLICY_VIOLATION",
                    "cannot create _brief.md in non-work-folder directory; use domain tools (wf_create) to initialize a work folder",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )
        else:
            fname = _brief_name_from_path(path)
            if fname in _CRITICAL_FILES:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"cannot create critical file {fname} via fs_create; use domain tools",
                    virtual_path=path, current_commit=self._commit(), retryable=False,
                )

        tools = self

        def _write(binding, args):
            if _is_brief_path(path):
                if resource_id is not None:
                    new_id = resource_id
                else:
                    new_id = tools._generate_new_id()
                final_content = tools._inject_id(content, new_id)
            else:
                final_content = content
                new_id = None
            binding.vfs.write(path, final_content, op="fs_create", args=args)
            if _is_brief_path(path):
                try:
                    r = parse_brief(final_content)
                    name = r["frontmatter"].get("title", "")
                except BriefError:
                    name = ""
            else:
                name = ""
            out = {
                "id": new_id,
                "name": name,
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_create", {
                "path": path, "content": content,
            }, _write, expected_base_commit,
                f"chore(wf): fs_create {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_commit,
                current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        content_final = self._vfs.read_text(path)
        st = self._vfs.stat(path)
        rid = _brief_id_from_content(content_final) if _is_brief_path(path) else None
        return _make_success(
            resource_id=rid,
            virtual_path=path,
            node_type="file",
            size=st["size"],
            content=content_final,
            commit=commit,
            git=result.get("git"),
            manifest=result.get("manifest"),
        )

    # ── fs_write ─────────────────────────────────────────────────────────────

    def fs_write(self, path: str, content: str,
                 folder_id: str | None = None,
                 resource_id: str | None = None,
                 expected_base_commit: str | None = None,
                 expected_resource_revision: str | None = None,
                 idempotency_key: str | None = None) -> dict:
        path, _fid_err = self._resolve_folder_id_path(folder_id, path)
        if _fid_err:
            return _fid_err
        commit = self._commit()

        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

        if _is_brief_path(path):
            eff_rid, rid_err = self._resolve_effective_resource_id(path, resource_id)
            if rid_err:
                return rid_err
        else:
            eff_rid = None
            if resource_id is not None:
                rid_err = self._validate_resource_id_for_path(path, resource_id)
                if rid_err:
                    return rid_err

        size_err = self._check_content_size(content, path)
        if size_err:
            return size_err

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        rev_err = self._check_revision(path, expected_resource_revision)
        if rev_err:
            return rev_err

        repl_err = self._check_resource_replaced(path, eff_rid)
        if repl_err:
            return repl_err

        hi_err = self._check_hard_invariants(path, "fs_write")
        if hi_err:
            return hi_err

        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"file not found (write does not implicitly create): {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=commit, retryable=False,
            )

        old_content = self._vfs.read_text(path)
        old_rid = _brief_id_from_content(old_content) if _is_brief_path(path) else None
        new_rid = _brief_id_from_content(content) if _is_brief_path(path) else None

        if _is_brief_path(path):
            if old_rid and new_rid and old_rid != new_rid:
                return _make_error(
                    "REF_MISMATCH",
                    f"id is immutable: expected {old_rid}, got {new_rid}",
                    resource_id=old_rid, virtual_path=path,
                    current_commit=commit, retryable=False,
                )

            if old_rid and not new_rid:
                content = self._inject_id(content, old_rid)
                new_rid = old_rid

            if not old_rid and new_rid:
                if self._check_duplicate_id(new_rid):
                    return _make_error(
                        "REF_MISMATCH",
                        f"id {new_rid} is already in use by another resource",
                        resource_id=new_rid, virtual_path=path,
                        current_commit=commit, retryable=False,
                    )

            err = self._validate_brief_content(content)
            if err:
                return _make_error(
                    "INVALID_CONTENT", err,
                    virtual_path=path, current_commit=commit, retryable=False,
                )

        fname = _brief_name_from_path(path)
        if fname in _CRITICAL_FILES or fname in _GOVERNED_FILES:
            gi_err = self._check_governed_invariants(path, "fs_write", old_content, content)
            if gi_err:
                return gi_err

        tools = self

        def _write(binding, args):
            binding.vfs.write(path, content, op="fs_write", args=args)
            if _is_brief_path(path):
                try:
                    r = parse_brief(content)
                    name = r["frontmatter"].get("title", "")
                except BriefError:
                    name = ""
            else:
                name = ""
            out = {
                "id": old_rid or new_rid,
                "name": name,
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_write", {
                "path": path, "content": content,
            }, _write, expected_base_commit,
                f"chore(wf): fs_write {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_commit,
                current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        st = self._vfs.stat(path)
        return _make_success(
            resource_id=old_rid or new_rid,
            virtual_path=path,
            node_type="file",
            size=st["size"],
            content=content,
            commit=commit,
            git=result.get("git"),
            manifest=result.get("manifest"),
        )

    # ── fs_edit ──────────────────────────────────────────────────────────────

    def fs_edit(self, path: str, old_string: str, new_string: str,
                folder_id: str | None = None,
                resource_id: str | None = None,
                replace_all: bool = False,
                expected_base_commit: str | None = None,
                expected_resource_revision: str | None = None,
                idempotency_key: str | None = None) -> dict:
        path, _fid_err = self._resolve_folder_id_path(folder_id, path)
        if _fid_err:
            return _fid_err
        commit = self._commit()

        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

        if _is_brief_path(path):
            eff_rid, rid_err = self._resolve_effective_resource_id(path, resource_id)
            if rid_err:
                return rid_err
        else:
            eff_rid = None
            if resource_id is not None:
                rid_err = self._validate_resource_id_for_path(path, resource_id)
                if rid_err:
                    return rid_err

        if not old_string:
            return _make_error(
                "INVALID_CONTENT", "old_string must be non-empty",
                virtual_path=path, current_commit=commit, retryable=False,
            )
        if old_string == new_string:
            return _make_error(
                "INVALID_CONTENT", "old_string must differ from new_string",
                virtual_path=path, current_commit=commit, retryable=False,
            )

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        rev_err = self._check_revision(path, expected_resource_revision)
        if rev_err:
            return rev_err

        hi_err = self._check_hard_invariants(path, "fs_edit")
        if hi_err:
            return hi_err

        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"file not found: {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            text = self._vfs.read_text(path)
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=commit, retryable=False,
            )

        old_rid = _brief_id_from_content(text) if _is_brief_path(path) else None
        count = text.count(old_string)
        if count == 0:
            return _make_error(
                "INVALID_CONTENT",
                f"old_string not found in file: {path}",
                resource_id=old_rid, virtual_path=path,
                current_commit=commit, retryable=False,
            )
        if count > 1 and not replace_all:
            return _make_error(
                "INVALID_CONTENT",
                f"old_string matches {count} times; narrow it or pass replace_all=True",
                resource_id=old_rid, virtual_path=path,
                current_commit=commit, retryable=False,
            )

        new_text = text.replace(old_string, new_string) if replace_all \
            else text.replace(old_string, new_string, 1)

        size_err = self._check_content_size(new_text, path)
        if size_err:
            return size_err

        if _is_brief_path(path):
            new_rid = _brief_id_from_content(new_text)
            if old_rid and new_rid and old_rid != new_rid:
                return _make_error(
                    "REF_MISMATCH",
                    f"id is immutable; edits that change the id field are rejected",
                    resource_id=old_rid, virtual_path=path,
                    current_commit=commit, retryable=False,
                )

            err = self._validate_brief_content(new_text)
            if err:
                return _make_error(
                    "INVALID_CONTENT", err,
                    resource_id=old_rid, virtual_path=path,
                    current_commit=commit, retryable=False,
                )
        else:
            new_rid = None

        repl_err = self._check_resource_replaced(path, eff_rid)
        if repl_err:
            return repl_err

        fname = _brief_name_from_path(path)
        if fname in _CRITICAL_FILES or fname in _GOVERNED_FILES:
            gi_err = self._check_governed_invariants(path, "fs_edit", text, new_text)
            if gi_err:
                return gi_err

        tools = self

        def _write(binding, args):
            binding.vfs.write(path, new_text, op="fs_edit", args=args)
            if _is_brief_path(path):
                try:
                    r = parse_brief(new_text)
                    name = r["frontmatter"].get("title", "")
                except BriefError:
                    name = ""
            else:
                name = ""
            result_id = old_rid or new_rid
            out = {
                "id": result_id,
                "name": name,
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_edit", {
                "path": path, "old_string": old_string, "new_string": new_string,
                "content": new_text,
            }, _write, expected_base_commit,
                f"chore(wf): fs_edit {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_commit,
                resource_id=old_rid, current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                resource_id=old_rid, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                resource_id=old_rid, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        st = self._vfs.stat(path)
        return _make_success(
            resource_id=old_rid or new_rid,
            virtual_path=path,
            node_type="file",
            size=st["size"],
            content=new_text,
            commit=commit,
            git=result.get("git"),
            manifest=result.get("manifest"),
        )

    # ── fs_copy ──────────────────────────────────────────────────────────────

    def fs_copy(self, source: str, dest: str,
                folder_id: str | None = None,
                resource_id: str | None = None,
                expected_base_commit: str | None = None,
                idempotency_key: str | None = None) -> dict:
        source, _fid_err_s = self._resolve_folder_id_path(folder_id, source)
        if _fid_err_s:
            return _fid_err_s
        dest, _fid_err_d = self._resolve_folder_id_path(folder_id, dest)
        if _fid_err_d:
            return _fid_err_d
        commit = self._commit()

        p_err_src = self._check_path(source)
        if p_err_src:
            return _make_error(
                "INVALID_PATH", p_err_src,
                virtual_path=source, current_commit=commit, retryable=False,
            )

        p_err = self._check_path(dest)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=dest, current_commit=commit, retryable=False,
            )

        for p in (source, dest):
            if _brief_name_from_path(p) in _CRITICAL_FILES:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"fs_copy cannot target critical file {_brief_name_from_path(p)}; use domain tools",
                    virtual_path=p, current_commit=commit, retryable=False,
                )

        source_is_brief = _is_brief_path(source)
        dest_is_brief = _is_brief_path(dest)

        if not source_is_brief and dest_is_brief:
            return _make_error(
                "POLICY_VIOLATION",
                "fs_copy cannot create a _brief.md identity from a non-brief file; use domain tools",
                virtual_path=dest, current_commit=commit, retryable=False,
            )

        if source_is_brief and not dest_is_brief:
            return _make_error(
                "POLICY_VIOLATION",
                "fs_copy cannot copy a _brief.md resource to a non-brief path; use domain tools",
                virtual_path=source, current_commit=commit, retryable=False,
            )

        if dest_is_brief and not self._is_valid_work_folder_dir(dest):
            return _make_error(
                "POLICY_VIOLATION",
                "fs_copy cannot create _brief.md in non-work-folder directory; use domain tools",
                virtual_path=dest, current_commit=commit, retryable=False,
            )

        if _is_brief_path(source):
            if resource_id is not None:
                rid_err = self._validate_resource_id_for_path(source, resource_id)
                if rid_err:
                    return rid_err

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        try:
            if not self._vfs.exists(source) or not self._vfs.is_file(source):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"source not found: {source}",
                    virtual_path=source, current_commit=commit, retryable=False,
                )
            if self._vfs.exists(dest):
                return _make_error(
                    "RESOURCE_EXISTS",
                    f"destination already exists: {dest}",
                    virtual_path=dest, current_commit=commit, retryable=False,
                )
            source_content = self._vfs.read_text(source)
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=source, current_commit=commit, retryable=False,
            )

        source_rid = _brief_id_from_content(source_content) if _is_brief_path(source) else None

        tools = self

        def _write(binding, args):
            if _is_brief_path(source) and _is_brief_path(dest):
                new_id = tools._generate_new_id()
                dest_content = tools._inject_id(source_content, new_id)
                page_err = tools._validate_brief_content(dest_content)
                if page_err:
                    raise ValueError(page_err)
            else:
                dest_content = source_content
                new_id = None
            binding.vfs.write(dest, dest_content, op="fs_copy", args=args)
            if _is_brief_path(dest):
                try:
                    r = parse_brief(dest_content)
                    name = r["frontmatter"].get("title", "")
                except BriefError:
                    name = ""
            else:
                name = ""
            out = {
                "id": new_id,
                "name": name,
                "changed_paths": [dest],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_copy", {
                "source": source, "dest": dest,
                "content": source_content,
            }, _write, expected_base_commit,
                f"chore(wf): fs_copy {source} -> {dest}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=source, expected_revision=expected_base_commit,
                resource_id=source_rid, current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                resource_id=source_rid, virtual_path=source,
                current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                resource_id=source_rid, virtual_path=source,
                current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        dest_content = self._vfs.read_text(dest)
        st = self._vfs.stat(dest)
        new_rid = _brief_id_from_content(dest_content) if _is_brief_path(dest) else None
        return _make_success(
            resource_id=new_rid,
            virtual_path=dest,
            node_type="file",
            size=st["size"],
            content=dest_content,
            commit=commit,
            git=result.get("git"),
            manifest=result.get("manifest"),
        )

    # ── fs_rename ────────────────────────────────────────────────────────────

    def fs_rename(self, source: str, dest: str,
                  folder_id: str | None = None,
                  resource_id: str | None = None,
                  expected_base_commit: str | None = None,
                  idempotency_key: str | None = None) -> dict:
        source, _fid_err_s = self._resolve_folder_id_path(folder_id, source)
        if _fid_err_s:
            return _fid_err_s
        dest, _fid_err_d = self._resolve_folder_id_path(folder_id, dest)
        if _fid_err_d:
            return _fid_err_d
        commit = self._commit()

        p_err_src = self._check_path(source)
        if p_err_src:
            return _make_error(
                "INVALID_PATH", p_err_src,
                virtual_path=source, current_commit=commit, retryable=False,
            )

        p_err = self._check_path(dest)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=dest, current_commit=commit, retryable=False,
            )

        for p in (source, dest):
            if _brief_name_from_path(p) in _CRITICAL_FILES:
                return _make_error(
                    "POLICY_VIOLATION",
                    f"fs_rename cannot involve critical file {_brief_name_from_path(p)}; use domain tools",
                    virtual_path=p, current_commit=commit, retryable=False,
                )

        source_is_brief = _is_brief_path(source)
        dest_is_brief = _is_brief_path(dest)

        if not source_is_brief and dest_is_brief:
            return _make_error(
                "POLICY_VIOLATION",
                "fs_rename cannot create a _brief.md identity from a non-brief file; use domain tools",
                virtual_path=dest, current_commit=commit, retryable=False,
            )

        if source_is_brief and not dest_is_brief:
            return _make_error(
                "POLICY_VIOLATION",
                "fs_rename cannot move a _brief.md resource to a non-brief destination; identity would be unresolvable",
                virtual_path=source, current_commit=commit, retryable=False,
            )

        if dest_is_brief and not self._is_valid_work_folder_dir(dest):
            return _make_error(
                "POLICY_VIOLATION",
                "fs_rename cannot move _brief.md into non-work-folder directory; use domain tools",
                virtual_path=dest, current_commit=commit, retryable=False,
            )

        if _is_brief_path(source):
            eff_rid, rid_err = self._resolve_effective_resource_id(source, resource_id)
            if rid_err:
                return rid_err
        else:
            eff_rid = None
            if resource_id is not None:
                rid_err = self._validate_resource_id_for_path(source, resource_id)
                if rid_err:
                    return rid_err

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        try:
            if not self._vfs.exists(source) or not self._vfs.is_file(source):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"source not found: {source}",
                    virtual_path=source, current_commit=commit, retryable=False,
                )
            if self._vfs.exists(dest):
                return _make_error(
                    "RESOURCE_EXISTS",
                    f"destination already exists: {dest}",
                    virtual_path=dest, current_commit=commit, retryable=False,
                )
            source_content = self._vfs.read_text(source)
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=source, current_commit=commit, retryable=False,
            )

        source_rid = _brief_id_from_content(source_content) if _is_brief_path(source) else None
        if _is_brief_path(source) and source_rid is None:
            return _make_error(
                "INVALID_CONTENT",
                "source file has no valid resource id",
                virtual_path=source, current_commit=commit, retryable=False,
            )

        hi_err = self._check_hard_invariants(source, "fs_rename")
        if hi_err:
            return hi_err

        tools = self

        def _write(binding, args):
            if _is_brief_path(source):
                try:
                    parsed = parse_brief(source_content)
                    fm = parsed["frontmatter"]
                    goal = parsed["goal"]
                    summary = parsed["summary"]
                except BriefError:
                    fm = {}
                    goal = ""
                    summary = ""
                updated_content = render_brief(
                    id=source_rid,
                    title=fm.get("title", ""),
                    status=fm.get("status", "active"),
                    created=fm.get("created", ""),
                    updated=fm.get("updated", ""),
                    goal=goal,
                    summary=summary,
                    tags=fm.get("tags") or (),
                    kind=fm.get("kind") or "",
                    links=fm.get("links") or (),
                )
                page_err = tools._validate_brief_content(updated_content)
                if page_err:
                    raise ValueError(page_err)
                binding.vfs.write(dest, updated_content, op="fs_rename", args=args)
            else:
                binding.vfs.write(dest, source_content, op="fs_rename", args=args)
            binding.vfs.delete(source, op="fs_rename", args=args)
            out = {
                "id": source_rid,
                "name": fm.get("title", "") if (_is_brief_path(source) and fm) else source_rid or "",
                "changed_paths": [source, dest],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_rename", {
                "source": source, "dest": dest,
            }, _write, expected_base_commit,
                f"chore(wf): fs_rename {source} -> {dest}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=source, expected_revision=expected_base_commit,
                resource_id=source_rid, current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                resource_id=source_rid, virtual_path=source,
                current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                resource_id=source_rid, virtual_path=source,
                current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        dest_content = self._vfs.read_text(dest)
        st = self._vfs.stat(dest)
        return _make_success(
            resource_id=source_rid,
            virtual_path=dest,
            node_type="file",
            size=st["size"],
            content=dest_content,
            commit=commit,
            git=result.get("git"),
            manifest=result.get("manifest"),
        )

    # ── fs_delete ────────────────────────────────────────────────────────────

    def fs_delete(self, path: str,
                  folder_id: str | None = None,
                  resource_id: str | None = None,
                  expected_base_commit: str | None = None,
                  idempotency_key: str | None = None) -> dict:
        path, _fid_err = self._resolve_folder_id_path(folder_id, path)
        if _fid_err:
            return _fid_err
        commit = self._commit()

        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

        if _is_brief_path(path):
            eff_rid, rid_err = self._resolve_effective_resource_id(path, resource_id)
            if rid_err:
                return rid_err
        else:
            eff_rid = None
            if resource_id is not None:
                rid_err = self._validate_resource_id_for_path(path, resource_id)
                if rid_err:
                    return rid_err

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        hi_err = self._check_hard_invariants(path, "fs_delete")
        if hi_err:
            return hi_err

        try:
            if not self._vfs.exists(path) or not self._vfs.is_file(path):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    f"file not found: {path}",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            content = self._vfs.read_text(path)
        except VFSError as e:
            return _make_error(
                "INVALID_PATH", str(e),
                virtual_path=path, current_commit=commit, retryable=False,
            )

        rid = _brief_id_from_content(content) if _is_brief_path(path) else None
        if _is_brief_path(path) and rid is None:
            return _make_error(
                "INVALID_CONTENT",
                "file has no valid resource id for tombstone",
                virtual_path=path, current_commit=commit, retryable=False,
            )

        tools = self

        def _write(binding, args):
            binding.vfs.delete(path, op="delete", args=args)
            out: dict[str, Any] = {
                "name": rid or "",
                "changed_paths": [path],
            }
            if rid is not None:
                out["id"] = rid
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("delete", {
                "path": path,
            }, _write, expected_base_commit,
                f"chore(wf): fs_delete {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_commit,
                resource_id=rid, current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "OPERATION_FAILED",
                str(e),
                resource_id=rid, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                resource_id=rid, virtual_path=path,
                current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        return _make_success(
            resource_id=rid,
            virtual_path=path,
            node_type="file",
            size=0,
            content=None,
            commit=commit,
            git=result.get("git"),
            manifest=result.get("manifest"),
        )

    # ── fs_batch ─────────────────────────────────────────────────────────────

    def fs_batch(self, operations: list[dict],
                 expected_base_commit: str | None = None,
                 idempotency_key: str | None = None) -> dict:
        commit = self._commit()
        if not operations:
            return _make_error(
                "INVALID_CONTENT",
                "batch operations list is empty",
                current_commit=commit, retryable=False,
            )

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

        for i, op_spec in enumerate(operations):
            preflight_err = self._preflight_batch_op(op_spec, i)
            if preflight_err is not None:
                return preflight_err

        tools = self

        def _write(binding, args):
            results: list[dict] = []
            all_changed_paths: list[str] = []
            tombstoned_ids: list[str] = []
            commit_msg_parts: list[str] = []

            for i, op_spec in enumerate(operations):
                op_name = op_spec.get("op", "")
                op_args = op_spec.get("args", {})

                if op_name == "fs_create":
                    path = op_args.get("path", "")
                    content = op_args.get("content", "")
                    resource_id = op_args.get("resource_id")
                    if _is_brief_path(path):
                        err = tools._validate_brief_content(content, allow_missing_id=True)
                        if err:
                            raise ValueError(f"batch op {i}: {err}")
                        if resource_id is not None:
                            new_id = resource_id
                        else:
                            new_id = binding.ledger.gen_id(tools._existing_ids())
                        final_content = tools._inject_id(content, new_id)
                    else:
                        final_content = content
                        new_id = None
                    binding.vfs.write(path, final_content, op="fs_create", args={"path": path})
                    all_changed_paths.append(path)
                    commit_msg_parts.append(f"create {path}")
                    results.append({"op": "fs_create", "path": path, "resource_id": new_id})

                elif op_name == "fs_write":
                    path = op_args.get("path", "")
                    content = op_args.get("content", "")
                    old_content = binding.vfs.read_text(path)
                    fname = _brief_name_from_path(path)

                    if fname in _CRITICAL_FILES or fname in _GOVERNED_FILES:
                        gi_err = tools._check_governed_invariants(path, "fs_write", old_content, content)
                        if gi_err:
                            raise BatchOpError(gi_err["code"], gi_err["message"])

                    if _is_brief_path(path):
                        old_rid = _brief_id_from_content(old_content)
                        new_rid = _brief_id_from_content(content)
                        if old_rid and new_rid and old_rid != new_rid:
                            raise BatchOpError("REF_MISMATCH", f"batch op {i}: id is immutable")
                        if old_rid and not new_rid:
                            content = tools._inject_id(content, old_rid)
                        err = tools._validate_brief_content(content)
                        if err:
                            raise ValueError(f"batch op {i}: {err}")
                        rid = _brief_id_from_content(content)
                    else:
                        rid = None
                    binding.vfs.write(path, content, op="fs_write", args={"path": path})
                    all_changed_paths.append(path)
                    commit_msg_parts.append(f"write {path}")
                    results.append({"op": "fs_write", "path": path, "resource_id": rid})

                elif op_name == "fs_edit":
                    path = op_args.get("path", "")
                    old_string = op_args.get("old_string", "")
                    new_string = op_args.get("new_string", "")
                    replace_all = op_args.get("replace_all", False)
                    text = binding.vfs.read_text(path)
                    count = text.count(old_string)
                    if count == 0:
                        raise ValueError(f"batch op {i}: old_string not found in file: {path}")
                    if count > 1 and not replace_all:
                        raise ValueError(f"batch op {i}: old_string matches {count} times")
                    new_text = text.replace(old_string, new_string) if replace_all \
                        else text.replace(old_string, new_string, 1)
                    if len(new_text.encode("utf-8")) > _MAX_FILE_SIZE:
                        raise BatchOpError("CONTENT_TOO_LARGE", f"batch op {i}: result exceeds max file size")

                    fname = _brief_name_from_path(path)
                    if fname in _CRITICAL_FILES or fname in _GOVERNED_FILES:
                        gi_err = tools._check_governed_invariants(path, "fs_edit", text, new_text)
                        if gi_err:
                            raise BatchOpError(gi_err["code"], gi_err["message"])

                    if _is_brief_path(path):
                        err = tools._validate_brief_content(new_text)
                        if err:
                            raise ValueError(f"batch op {i}: {err}")
                        new_rid = _brief_id_from_content(new_text)
                        old_rid = _brief_id_from_content(text)
                        if old_rid and new_rid and old_rid != new_rid:
                            raise BatchOpError("REF_MISMATCH", f"batch op {i}: id is immutable")
                    else:
                        old_rid = None
                        new_rid = None
                    binding.vfs.write(path, new_text, op="fs_edit", args={"path": path})
                    all_changed_paths.append(path)
                    commit_msg_parts.append(f"edit {path}")
                    results.append({"op": "fs_edit", "path": path, "resource_id": new_rid or old_rid})

                elif op_name == "fs_copy":
                    source = op_args.get("source", "")
                    dest = op_args.get("dest", "")
                    source_content = binding.vfs.read_text(source)
                    source_is_brief = _is_brief_path(source)
                    dest_is_brief = _is_brief_path(dest)
                    if not source_is_brief and dest_is_brief:
                        raise BatchOpError("POLICY_VIOLATION",
                            f"batch op {i}: cannot create a _brief.md identity from a non-brief file")
                    if source_is_brief and not dest_is_brief:
                        raise BatchOpError("POLICY_VIOLATION",
                            f"batch op {i}: cannot copy a _brief.md resource to a non-brief path")
                    if source_is_brief and dest_is_brief:
                        new_id = binding.ledger.gen_id(tools._existing_ids())
                        dest_content = tools._inject_id(source_content, new_id)
                        page_err = tools._validate_brief_content(dest_content)
                        if page_err:
                            raise ValueError(f"batch op {i}: {page_err}")
                    else:
                        dest_content = source_content
                        new_id = None

                    # Enforce governed invariants for non-brief dest files that are governed
                    fname = _brief_name_from_path(dest)
                    if not dest_is_brief and fname in _GOVERNED_FILES:
                        gi_err = tools._check_governed_invariants(dest, "fs_write", None, dest_content)
                        if gi_err:
                            raise BatchOpError(gi_err["code"], gi_err["message"])

                    binding.vfs.write(dest, dest_content, op="fs_copy", args={"path": dest})
                    all_changed_paths.append(dest)
                    commit_msg_parts.append(f"copy {source} -> {dest}")
                    results.append({"op": "fs_copy", "source": source, "dest": dest, "resource_id": new_id})

                elif op_name == "fs_rename":
                    source = op_args.get("source", "")
                    dest = op_args.get("dest", "")
                    source_content = binding.vfs.read_text(source)
                    source_is_brief = _is_brief_path(source)
                    dest_is_brief = _is_brief_path(dest)
                    if not source_is_brief and dest_is_brief:
                        raise BatchOpError("POLICY_VIOLATION",
                            f"batch op {i}: cannot create a _brief.md identity from a non-brief file")
                    if source_is_brief and not dest_is_brief:
                        raise BatchOpError("POLICY_VIOLATION",
                            f"batch op {i}: cannot move a _brief.md resource to a non-brief destination")
                    if source_is_brief:
                        rid = _brief_id_from_content(source_content)
                        if rid is None:
                            raise ValueError(f"batch op {i}: source file has no valid resource id")
                        try:
                            parsed = parse_brief(source_content)
                            fm = parsed["frontmatter"]
                            goal = parsed["goal"]
                            summary = parsed["summary"]
                        except BriefError:
                            fm = {}
                            goal = ""
                            summary = ""
                        updated_content = render_brief(
                            id=rid,
                            title=fm.get("title", ""),
                            status=fm.get("status", "active"),
                            created=fm.get("created", ""),
                            updated=fm.get("updated", ""),
                            goal=goal,
                            summary=summary,
                            tags=fm.get("tags") or (),
                            kind=fm.get("kind") or "",
                            links=fm.get("links") or (),
                        )
                        page_err = tools._validate_brief_content(updated_content)
                        if page_err:
                            raise ValueError(f"batch op {i}: {page_err}")
                        binding.vfs.write(dest, updated_content, op="fs_rename", args={"path": dest})
                    else:
                        rid = None
                        binding.vfs.write(dest, source_content, op="fs_rename", args={"path": dest})
                    binding.vfs.delete(source, op="fs_rename", args={"path": dest})
                    all_changed_paths.extend([source, dest])
                    commit_msg_parts.append(f"rename {source} -> {dest}")
                    results.append({"op": "fs_rename", "source": source, "dest": dest, "resource_id": rid})

                elif op_name == "fs_delete":
                    path = op_args.get("path", "")
                    content = binding.vfs.read_text(path)
                    if _is_brief_path(path):
                        rid = _brief_id_from_content(content)
                        if rid is None:
                            raise ValueError(f"batch op {i}: file has no valid resource id")
                    else:
                        rid = None
                    binding.vfs.delete(path, op="fs_delete", args={"path": path})
                    all_changed_paths.append(path)
                    if rid is not None:
                        tombstoned_ids.append(rid)
                    commit_msg_parts.append(f"delete {path}")
                    results.append({"op": "fs_delete", "path": path, "resource_id": rid})

            commit_msg = "chore(wf): fs_batch: " + "; ".join(commit_msg_parts)
            out = {
                "id": "batch",
                "name": "batch",
                "changed_paths": all_changed_paths,
                "batch_results": results,
                "commit_msg": commit_msg,
                "tombstoned_ids": tombstoned_ids,
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_batch", {
                "operations": operations,
            }, _write, expected_base_commit,
                f"chore(wf): fs_batch")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                expected_revision=expected_base_commit,
                current_commit=self._commit(), retryable=True,
            )
        except BatchOpError as e:
            _restore_tree(self._repo_root)
            return _make_error(
                e.code, str(e),
                current_commit=self._commit(), retryable=False,
            )
        except PolicyViolationError as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "POLICY_VIOLATION", str(e),
                current_commit=self._commit(), retryable=False,
            )
        except ValueError as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "INVALID_CONTENT", str(e),
                current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            _restore_tree(self._repo_root)
            return _make_error(
                "OPERATION_FAILED", str(e),
                current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        return _make_success(
            resource_id=None,
            virtual_path="",
            node_type="batch",
            size=None,
            content=None,
            commit=commit,
            batch_results=result.get("batch_results", []),
            git=result.get("git"),
            manifest=result.get("manifest"),
        )