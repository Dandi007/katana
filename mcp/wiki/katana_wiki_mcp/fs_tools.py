"""FSTools: Full VFS (fs_*) tool surface for the Wiki app.

Maps design §5.2 operation set to M1 kernel's GovernedVFS (read/discovery)
and GovernedKernel.mutate (write/transaction), with unified success/error envelopes.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from katana_kernel import (
    CASRejectionError,
    DomainPolicy,
    GovernedKernel,
    GovernedVFS,
    MutationBrokenError,
    ResourceIdLedger,
    TransactionManifest,
    head_sha,
)
from katana_kernel.gitops import cas_guard
from katana_kernel.policy import PolicyViolationError
from katana_kernel.vfs import VFSError
from katana_wiki_mcp import invariants as _inv
from katana_wiki_mcp.pages import parse_page, render_page

ID_RE = re.compile(r"w-[0-9a-f]{6}")

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
    "BROKEN",
}

_EXCLUDE_DIRS = {".git", ".obsidian", ".wiki", ".trash", ".katana"}


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


def _make_operation_error(exc: Exception, **context: Any) -> dict:
    if isinstance(exc, MutationBrokenError):
        return exc.as_error(**context)
    return _make_error("OPERATION_FAILED", str(exc), **context)


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
        env["content"] = content_field
    if total_lines is not None:
        env["total_lines"] = total_lines
    if offset is not None:
        env["offset"] = offset
    if limit is not None:
        env["limit"] = limit
    env.update(extra)
    return env


def _page_id_from_content(content: str) -> str | None:
    fm, _ = parse_page(content)
    return fm.get("id")


class FSTools:
    def __init__(self, kernel: GovernedKernel, repo_root: str):
        self._kernel = kernel
        self._binding = kernel.get_binding("wiki")
        self._repo_root = repo_root
        self._vfs: GovernedVFS = self._binding.vfs

    def _commit(self) -> str:
        return head_sha(self._repo_root) or ""

    def _resolve_path(self, path_or_id: str) -> tuple[str | None, str | None, str | None]:
        if ID_RE.fullmatch(path_or_id):
            return self._resolve_by_id(path_or_id)
        return self._resolve_by_path(path_or_id)

    def _resolve_by_id(self, resource_id: str) -> tuple[str | None, str | None, str | None]:
        pages = self._scan()
        for p in pages:
            if p["id"] == resource_id:
                path = p["path"]
                content = self._vfs.read_text(path)
                return (path, resource_id, content)
        return (None, None, None)

    def _resolve_by_path(self, virtual_path: str) -> tuple[str | None, str | None, str | None]:
        try:
            if not self._vfs.exists(virtual_path) or not self._vfs.is_file(virtual_path):
                return (None, None, None)
            content = self._vfs.read_text(virtual_path)
            rid = _page_id_from_content(content)
            return (virtual_path, rid, content)
        except VFSError:
            return (None, None, None)

    def _valid_page(self, content: str) -> bool:
        fm, _ = parse_page(content)
        return bool(fm.get("id") and fm.get("tags") and fm.get("类型"))

    def _existing_ids(self) -> set[str]:
        pages = self._scan()
        return {p["id"] for p in pages}

    def _scan(self) -> list[dict]:
        pages = []
        for p in self._vfs.ls("**/*.md"):
            parts = p.replace("\\", "/").split("/")
            if parts[0] in _EXCLUDE_DIRS:
                continue
            try:
                text = self._vfs.read_text(p)
            except Exception:
                continue
            fm, _ = parse_page(text)
            pid = fm.get("id")
            if not pid or not fm.get("tags") or not fm.get("类型"):
                continue
            fm["path"] = p
            pages.append(fm)
        return pages

    def _find_by_path(self, virtual_path: str) -> dict | None:
        try:
            if not self._vfs.exists(virtual_path) or not self._vfs.is_file(virtual_path):
                return None
            content = self._vfs.read_text(virtual_path)
            fm, _ = parse_page(content)
            if not fm.get("id"):
                return None
            fm["path"] = virtual_path
            return fm
        except VFSError:
            return None

    def _call_mutate(self, op: str, args: dict, write_fn, expected_base_sha: str | None,
                     commit_msg: str) -> dict:
        return self._kernel.mutate(
            "wiki", op, args,
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
        if resource_id is None:
            manifests = self._binding.manifest.list_manifests()
            for m in manifests:
                if m.get("op") == "delete" and path in m.get("changed_paths", []):
                    m_result = m.get("result", {})
                    deleted_id = m_result.get("id")
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
            return "path must not contain '..' or absolute paths"
        if path.startswith("."):
            return "path must not start with '.'"
        parts = path.replace("\\", "/").split("/")
        if parts[0] in _EXCLUDE_DIRS:
            return f"path must not be inside excluded directory: {parts[0]}"
        return None

    def _validate_page_content(self, content: str, allow_missing_id: bool = False) -> str | None:
        fm, body = parse_page(content)
        if not fm and not body:
            return "content is not a valid page (unparseable frontmatter)"
        if not allow_missing_id and not fm.get("id"):
            return "page must have an id"
        if fm.get("id") and not ID_RE.fullmatch(fm["id"]):
            return f"invalid id format: {fm['id']}"
        errs = _inv.validate_page(fm, body, require_summary=True, require_sources=True)
        if errs:
            return "; ".join(errs)
        return None

    def _generate_new_id(self) -> str:
        return self._binding.ledger.gen_id(self._existing_ids())

    def _inject_id(self, content: str, new_id: str) -> str:
        fm, body = parse_page(content)
        fm["id"] = new_id
        return render_page(fm, body)

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
            rid = _page_id_from_content(content)
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
                    old_rid = _page_id_from_content(old_content)
                    if old_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match path {path} (found {old_rid})",
                            resource_id=resource_id, virtual_path=path,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
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
            old_string = op_args.get("old_string", "")
            replace_all = op_args.get("replace_all", False)
            text = self._vfs.read_text(path)
            count = text.count(old_string) if old_string else 0
            if count == 0 or (count > 1 and not replace_all):
                return _make_error(
                    "INVALID_CONTENT",
                    f"batch op {index}: old_string must match exactly",
                    virtual_path=path, current_commit=commit, retryable=False,
                )
            if resource_id is not None:
                try:
                    old_content = self._vfs.read_text(path)
                    old_rid = _page_id_from_content(old_content)
                    if old_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match path {path} (found {old_rid})",
                            resource_id=resource_id, virtual_path=path,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
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
            if resource_id is not None:
                try:
                    src_content = self._vfs.read_text(source)
                    src_rid = _page_id_from_content(src_content)
                    if src_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match source {source} (found {src_rid})",
                            resource_id=resource_id, virtual_path=source,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass
            source_content = self._vfs.read_text(source)
            content_error = self._validate_page_content(source_content)
            if content_error:
                return _make_error(
                    "INVALID_CONTENT",
                    f"batch op {index}: {content_error}",
                    virtual_path=source, current_commit=commit, retryable=False,
                )

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
            if resource_id is not None:
                try:
                    src_content = self._vfs.read_text(source)
                    src_rid = _page_id_from_content(src_content)
                    if src_rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match source {source} (found {src_rid})",
                            resource_id=resource_id, virtual_path=source,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
                    pass
            if _page_id_from_content(self._vfs.read_text(source)) is None:
                return _make_error(
                    "INVALID_CONTENT",
                    f"batch op {index}: source has no resource id",
                    virtual_path=source, current_commit=commit, retryable=False,
                )

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
            if resource_id is not None:
                try:
                    content = self._vfs.read_text(path)
                    rid = _page_id_from_content(content)
                    if rid != resource_id:
                        return _make_error(
                            "REF_MISMATCH",
                            f"batch op {index}: resource_id {resource_id} does not match path {path} (found {rid})",
                            resource_id=resource_id, virtual_path=path,
                            current_commit=commit, retryable=False,
                        )
                except VFSError:
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
                "idempotency": "idempotency_key + expected_base_sha + expected_resource_revision",
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
            rid = _page_id_from_content(content)
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
            rid = _page_id_from_content(content)
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

        rid = _page_id_from_content(content)
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
                  resource_id: str | None = None,
                  expected_base_sha: str | None = None,
                  idempotency_key: str | None = None) -> dict:
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

        err = self._validate_page_content(content, allow_missing_id=True)
        if err:
            return _make_error(
                "INVALID_CONTENT", err,
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )

        tools = self

        def _write(binding, args):
            if resource_id is not None:
                new_id = resource_id
            else:
                new_id = tools._generate_new_id()
            final_content = tools._inject_id(content, new_id)
            binding.vfs.write(path, final_content, op="fs_create", args=args)
            fm, _ = parse_page(final_content)
            out = {
                "id": new_id,
                "name": fm.get("title", ""),
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_create", {
                "path": path, "content": content,
            }, _write, expected_base_sha,
                f"chore(wiki): fs_create {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_sha,
                current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            return _make_operation_error(
                e,
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        content_final = self._vfs.read_text(path)
        st = self._vfs.stat(path)
        rid = _page_id_from_content(content_final)
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
                 resource_id: str | None = None,
                 expected_base_sha: str | None = None,
                 expected_resource_revision: str | None = None,
                 idempotency_key: str | None = None) -> dict:
        commit = self._commit()

        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

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

        repl_err = self._check_resource_replaced(path, resource_id)
        if repl_err:
            return repl_err

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
        old_rid = _page_id_from_content(old_content)
        new_rid = _page_id_from_content(content)

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

        err = self._validate_page_content(content)
        if err:
            return _make_error(
                "INVALID_CONTENT", err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

        tools = self

        def _write(binding, args):
            binding.vfs.write(path, content, op="fs_write", args=args)
            fm, _ = parse_page(content)
            out = {
                "id": old_rid or (fm.get("id") if fm else None),
                "name": fm.get("title", "") if fm else "",
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_write", {
                "path": path, "content": content,
            }, _write, expected_base_sha,
                f"chore(wiki): fs_write {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_sha,
                current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT",
                str(e),
                virtual_path=path, current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            return _make_operation_error(
                e,
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
                resource_id: str | None = None,
                replace_all: bool = False,
                expected_base_sha: str | None = None,
                expected_resource_revision: str | None = None,
                idempotency_key: str | None = None) -> dict:
        commit = self._commit()

        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

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

        old_rid = _page_id_from_content(text)
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

        new_rid = _page_id_from_content(new_text)
        if old_rid and new_rid and old_rid != new_rid:
            return _make_error(
                "REF_MISMATCH",
                f"id is immutable; edits that change the id field are rejected",
                resource_id=old_rid, virtual_path=path,
                current_commit=commit, retryable=False,
            )

        err = self._validate_page_content(new_text)
        if err:
            return _make_error(
                "INVALID_CONTENT", err,
                resource_id=old_rid, virtual_path=path,
                current_commit=commit, retryable=False,
            )

        repl_err = self._check_resource_replaced(path, old_rid)
        if repl_err:
            return repl_err

        tools = self

        def _write(binding, args):
            binding.vfs.write(path, new_text, op="fs_edit", args=args)
            fm, _ = parse_page(new_text)
            out = {
                "id": old_rid or (fm.get("id") if fm else None),
                "name": fm.get("title", "") if fm else "",
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_edit", {
                "path": path, "old_string": old_string, "new_string": new_string,
                "content": new_text,
            }, _write, expected_base_sha,
                f"chore(wiki): fs_edit {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_sha,
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
            return _make_operation_error(
                e,
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
                resource_id: str | None = None,
                expected_base_sha: str | None = None,
                idempotency_key: str | None = None) -> dict:
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

        source_rid = _page_id_from_content(source_content)

        tools = self

        def _write(binding, args):
            new_id = tools._generate_new_id()
            dest_content = tools._inject_id(source_content, new_id)
            page_err = tools._validate_page_content(dest_content)
            if page_err:
                raise ValueError(page_err)
            binding.vfs.write(dest, dest_content, op="fs_copy", args=args)
            fm, _ = parse_page(dest_content)
            out = {
                "id": new_id,
                "name": fm.get("title", "") if fm else "",
                "changed_paths": [dest],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_copy", {
                "source": source, "dest": dest,
                "content": source_content,
            }, _write, expected_base_sha,
                f"chore(wiki): fs_copy {source} -> {dest}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=source, expected_revision=expected_base_sha,
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
            return _make_operation_error(
                e,
                resource_id=source_rid, virtual_path=source,
                current_commit=self._commit(), retryable=False,
            )

        commit = self._commit()
        dest_content = self._vfs.read_text(dest)
        st = self._vfs.stat(dest)
        new_rid = _page_id_from_content(dest_content)
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
                  resource_id: str | None = None,
                  expected_base_sha: str | None = None,
                  idempotency_key: str | None = None) -> dict:
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

        source_rid = _page_id_from_content(source_content)
        if source_rid is None:
            return _make_error(
                "INVALID_CONTENT",
                "source file has no valid resource id",
                virtual_path=source, current_commit=commit, retryable=False,
            )

        tools = self

        def _write(binding, args):
            fm, body = parse_page(source_content)
            updated_content = render_page(fm, body)
            page_err = tools._validate_page_content(updated_content)
            if page_err:
                raise ValueError(page_err)
            binding.vfs.write(dest, updated_content, op="fs_rename", args=args)
            binding.vfs.delete(source, op="fs_rename", args=args)
            out = {
                "id": source_rid,
                "name": fm.get("title", "") if fm else source_rid,
                "changed_paths": [source, dest],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("fs_rename", {
                "source": source, "dest": dest,
            }, _write, expected_base_sha,
                f"chore(wiki): fs_rename {source} -> {dest}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=source, expected_revision=expected_base_sha,
                resource_id=source_rid, current_commit=self._commit(), retryable=True,
            )
        except (PolicyViolationError, ValueError) as e:
            return _make_error(
                "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "OPERATION_FAILED",
                str(e),
                resource_id=source_rid, virtual_path=source,
                current_commit=self._commit(), retryable=False,
            )
        except Exception as e:
            return _make_operation_error(
                e,
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
                  resource_id: str | None = None,
                  expected_base_sha: str | None = None,
                  idempotency_key: str | None = None) -> dict:
        commit = self._commit()

        p_err = self._check_path(path)
        if p_err:
            return _make_error(
                "INVALID_PATH", p_err,
                virtual_path=path, current_commit=commit, retryable=False,
            )

        rid_err = self._validate_resource_id_for_path(path, resource_id)
        if rid_err:
            return rid_err

        idem_err = self._check_idempotency(idempotency_key)
        if idem_err:
            return idem_err

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

        rid = _page_id_from_content(content)
        if rid is None:
            return _make_error(
                "INVALID_CONTENT",
                "file has no valid resource id for tombstone",
                virtual_path=path, current_commit=commit, retryable=False,
            )

        tools = self

        def _write(binding, args):
            binding.vfs.delete(path, op="delete", args=args)
            out = {
                "id": rid,
                "name": rid,
                "changed_paths": [path],
            }
            if idempotency_key:
                out["idempotency_key"] = idempotency_key
            return out

        try:
            result = self._call_mutate("delete", {
                "path": path,
            }, _write, expected_base_sha,
                f"chore(wiki): fs_delete {path}")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                virtual_path=path, expected_revision=expected_base_sha,
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
            return _make_operation_error(
                e,
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
                    err = tools._validate_page_content(content, allow_missing_id=True)
                    if err:
                        raise ValueError(f"batch op {i}: {err}")
                    if resource_id is not None:
                        new_id = resource_id
                    else:
                        new_id = binding.ledger.gen_id(tools._existing_ids())
                    final_content = tools._inject_id(content, new_id)
                    binding.vfs.write(path, final_content, op="fs_create", args={"path": path})
                    all_changed_paths.append(path)
                    commit_msg_parts.append(f"create {path}")
                    results.append({"op": "fs_create", "path": path, "resource_id": new_id})

                elif op_name == "fs_write":
                    path = op_args.get("path", "")
                    content = op_args.get("content", "")
                    old_content = binding.vfs.read_text(path)
                    old_rid = _page_id_from_content(old_content)
                    new_rid = _page_id_from_content(content)
                    if old_rid and new_rid and old_rid != new_rid:
                        raise ValueError(f"batch op {i}: id is immutable")
                    if old_rid and not new_rid:
                        content = tools._inject_id(content, old_rid)
                    err = tools._validate_page_content(content)
                    if err:
                        raise ValueError(f"batch op {i}: {err}")
                    binding.vfs.write(path, content, op="fs_write", args={"path": path})
                    all_changed_paths.append(path)
                    commit_msg_parts.append(f"write {path}")
                    rid = _page_id_from_content(content)
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
                        raise ValueError(f"batch op {i}: result exceeds max file size")
                    err = tools._validate_page_content(new_text)
                    if err:
                        raise ValueError(f"batch op {i}: {err}")
                    new_rid = _page_id_from_content(new_text)
                    old_rid = _page_id_from_content(text)
                    if old_rid and new_rid and old_rid != new_rid:
                        raise ValueError(f"batch op {i}: id is immutable")
                    binding.vfs.write(path, new_text, op="fs_edit", args={"path": path})
                    all_changed_paths.append(path)
                    commit_msg_parts.append(f"edit {path}")
                    results.append({"op": "fs_edit", "path": path, "resource_id": new_rid or old_rid})

                elif op_name == "fs_copy":
                    source = op_args.get("source", "")
                    dest = op_args.get("dest", "")
                    source_content = binding.vfs.read_text(source)
                    new_id = binding.ledger.gen_id(tools._existing_ids())
                    dest_content = tools._inject_id(source_content, new_id)
                    page_err = tools._validate_page_content(dest_content)
                    if page_err:
                        raise ValueError(f"batch op {i}: {page_err}")
                    binding.vfs.write(dest, dest_content, op="fs_copy", args={"path": dest})
                    all_changed_paths.append(dest)
                    commit_msg_parts.append(f"copy {source} -> {dest}")
                    results.append({"op": "fs_copy", "source": source, "dest": dest, "resource_id": new_id})

                elif op_name == "fs_rename":
                    source = op_args.get("source", "")
                    dest = op_args.get("dest", "")
                    source_content = binding.vfs.read_text(source)
                    rid = _page_id_from_content(source_content)
                    if rid is None:
                        raise ValueError(f"batch op {i}: source file has no valid resource id")
                    fm, body = parse_page(source_content)
                    updated_content = render_page(fm, body)
                    page_err = tools._validate_page_content(updated_content)
                    if page_err:
                        raise ValueError(f"batch op {i}: {page_err}")
                    binding.vfs.write(dest, updated_content, op="fs_rename", args={"path": dest})
                    binding.vfs.delete(source, op="fs_rename", args={"path": dest})
                    all_changed_paths.extend([source, dest])
                    commit_msg_parts.append(f"rename {source} -> {dest}")
                    results.append({"op": "fs_rename", "source": source, "dest": dest, "resource_id": rid})

                elif op_name == "fs_delete":
                    path = op_args.get("path", "")
                    content = binding.vfs.read_text(path)
                    rid = _page_id_from_content(content)
                    if rid is None:
                        raise ValueError(f"batch op {i}: file has no valid resource id")
                    binding.vfs.delete(path, op="fs_delete", args={"path": path})
                    all_changed_paths.append(path)
                    tombstoned_ids.append(rid)
                    commit_msg_parts.append(f"delete {path}")
                    results.append({"op": "fs_delete", "path": path, "resource_id": rid})

            commit_msg = "chore(wiki): fs_batch: " + "; ".join(commit_msg_parts)
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
                f"chore(wiki): fs_batch")
        except CASRejectionError as e:
            return _make_error(
                "BASE_COMMIT_CONFLICT", str(e),
                expected_revision=expected_base_commit,
                current_commit=self._commit(), retryable=True,
            )
        except MutationBrokenError as e:
            return _make_operation_error(
                e,
                current_commit=self._commit(), retryable=False,
            )
        except (PolicyViolationError, ValueError) as e:
            code = "POLICY_VIOLATION" if isinstance(e, PolicyViolationError) else "INVALID_CONTENT"
            return _make_error(
                code, str(e),
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
