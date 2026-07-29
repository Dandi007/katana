"""Work Folder 的 ID-only Full VFS。

公共契约只接受 ``folder_id`` 与 folder-relative filename。内部 repo path 只在本
模块内短暂拼接，永不进入 success/error envelope。扁平布局使 folder resolve 为
O(1)：``wf-ID/_brief.md`` 必须存在且 ``brief.id == folder_id``。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from katana_kernel import (
    CASRejectionError,
    GovernedKernel,
    GovernedVFS,
    MutationBrokenError,
    head_sha,
)
from katana_kernel.policy import PolicyViolationError
from katana_kernel.vfs import VFSError
from katana_work_folder_mcp.brief import (
    BRIEF_NAME,
    VALID_STATUS,
    BriefError,
    parse_brief,
    validate_brief,
)

ID_RE = re.compile(r"^wf-[0-9a-f]{6}$")

_MAX_FILE_SIZE = 1_000_000
_EXCLUDE_PARTS = {".git", ".katana"}
_CRITICAL_FILES = {"progress.md", "golden-order.md"}
_GOVERNED_FILES = {"context.md", "CLAUDE.md", "AGENTS.md"}
_ERROR_CODES = {
    "BASE_COMMIT_CONFLICT",
    "BROKEN",
    "CONTENT_TOO_LARGE",
    "IDEMPOTENCY_CONFLICT",
    "INVALID_CONTENT",
    "INVALID_PATH",
    "OPERATION_FAILED",
    "POLICY_VIOLATION",
    "RESOURCE_EXISTS",
    "RESOURCE_NOT_FOUND",
    "RESOURCE_REPLACED",
    "REVISION_CONFLICT",
}


class BatchOpError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_error(
    code: str,
    message: str,
    *,
    folder_id: str | None = None,
    filename: str | None = None,
    expected_revision: str | None = None,
    actual_revision: str | None = None,
    current_commit: str | None = None,
    retryable: bool = False,
) -> dict:
    if code not in _ERROR_CODES:
        code = "OPERATION_FAILED"
    result: dict[str, Any] = {
        "ok": False,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if folder_id is not None:
        result["folder_id"] = folder_id
    if filename is not None:
        result["filename"] = filename
    if expected_revision is not None:
        result["expected_revision"] = expected_revision
    if actual_revision is not None:
        result["actual_revision"] = actual_revision
    if current_commit is not None:
        result["current_commit"] = current_commit
    return result


def _mutation_id(result: dict) -> str | None:
    manifest = result.get("manifest")
    if isinstance(manifest, dict):
        value = manifest.get("manifest_id")
        return str(value) if value else None
    return None


def _make_success(
    *,
    folder_id: str | None = None,
    filename: str | None = None,
    node_type: str | None = None,
    size: int | None = None,
    content: str | None = None,
    content_revision: str | None = None,
    commit: str | None = None,
    entries: list[dict] | None = None,
    capabilities: dict | None = None,
    batch_results: list[dict] | None = None,
    git: dict | None = None,
    mutation_id: str | None = None,
) -> dict:
    result: dict[str, Any] = {"ok": True}
    if folder_id is not None:
        result["folder_id"] = folder_id
    if filename is not None:
        result["filename"] = filename
    if node_type is not None:
        result["node_type"] = node_type
    if size is not None:
        result["size"] = size
    if content is not None:
        result["content"] = content
    if content_revision is not None:
        result["content_revision"] = content_revision
    if commit is not None:
        result["commit"] = commit
    if entries is not None:
        result["entries"] = entries
    if capabilities is not None:
        result["capabilities"] = capabilities
    if batch_results is not None:
        result["batch_results"] = batch_results
    if git is not None:
        result["git"] = git
    if mutation_id is not None:
        result["mutation_id"] = mutation_id
    return result


def _basename(filename: str) -> str:
    return filename.rsplit("/", 1)[-1]


def _extract_changelog_section(content: str) -> tuple[str, str]:
    index = content.find("## Changelog")
    if index == -1:
        return content, ""
    return content[:index], content[index:]


def _has_heading(content: str, heading: str) -> bool:
    return re.search(rf"^{re.escape(heading)}\s*$", content, re.MULTILINE) is not None


class FSTools:
    def __init__(self, kernel: GovernedKernel, repo_root: str):
        self._kernel = kernel
        self._binding = kernel.get_binding("work-folder")
        self._repo_root = repo_root
        self._vfs: GovernedVFS = self._binding.vfs

    def _commit(self) -> str:
        return head_sha(self._repo_root) or ""

    def _validate_folder(self, folder_id: str) -> tuple[str | None, dict | None]:
        commit = self._commit()
        if not isinstance(folder_id, str) or not ID_RE.fullmatch(folder_id):
            return None, _make_error(
                "INVALID_PATH",
                "folder_id must match wf-<6 lowercase hex>",
                folder_id=folder_id if isinstance(folder_id, str) else None,
                current_commit=commit,
            )
        if self._binding.ledger.is_tombstoned(folder_id):
            return None, _make_error(
                "RESOURCE_REPLACED",
                "work folder was deleted",
                folder_id=folder_id,
                current_commit=commit,
            )
        try:
            if not self._vfs.exists(folder_id) or not self._vfs.is_dir(folder_id):
                return None, _make_error(
                    "RESOURCE_NOT_FOUND",
                    "work folder not found",
                    folder_id=folder_id,
                    current_commit=commit,
                )
            brief_name = f"{folder_id}/{BRIEF_NAME}"
            if not self._vfs.exists(brief_name) or not self._vfs.is_file(brief_name):
                return None, _make_error(
                    "INVALID_CONTENT",
                    f"work folder is missing {BRIEF_NAME}",
                    folder_id=folder_id,
                    current_commit=commit,
                )
            parsed = parse_brief(self._vfs.read_text(brief_name))
            brief_id = parsed["frontmatter"].get("id")
            if brief_id != folder_id:
                return None, _make_error(
                    "INVALID_CONTENT",
                    "folder identity does not match _brief.md",
                    folder_id=folder_id,
                    filename=BRIEF_NAME,
                    current_commit=commit,
                )
        except (BriefError, VFSError) as exc:
            return None, _make_error(
                "INVALID_CONTENT",
                f"invalid work folder identity: {exc}",
                folder_id=folder_id,
                filename=BRIEF_NAME,
                current_commit=commit,
            )
        return folder_id, None

    def _validate_filename(
        self,
        filename: str,
        *,
        allow_empty: bool = False,
    ) -> str | None:
        if not isinstance(filename, str):
            return "filename must be a string"
        if filename == "":
            return None if allow_empty else "filename is required"
        if "\\" in filename:
            return "filename must use '/' separators"
        if filename.startswith("/") or filename.endswith("/"):
            return "filename must be folder-relative and canonical"
        parts = filename.split("/")
        if any(part in ("", ".", "..") for part in parts):
            return "filename must not contain empty, '.' or '..' segments"
        if any(part.startswith(".") or part in _EXCLUDE_PARTS for part in parts):
            return "filename must not target hidden or governed metadata"
        return None

    def _resolve(
        self,
        folder_id: str,
        filename: str,
        *,
        allow_empty: bool = False,
    ) -> tuple[str | None, dict | None]:
        folder, error = self._validate_folder(folder_id)
        if error:
            return None, error
        filename_error = self._validate_filename(filename, allow_empty=allow_empty)
        if filename_error:
            return None, _make_error(
                "INVALID_PATH",
                filename_error,
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        return folder if not filename else f"{folder}/{filename}", None

    def _call_mutate(
        self,
        op: str,
        args: dict,
        write_fn,
        expected_base_commit: str | None,
        commit_msg: str,
    ) -> dict:
        return self._kernel.mutate(
            "work-folder",
            op,
            args,
            expected_base_sha=expected_base_commit,
            write_fn=write_fn,
            commit_msg=commit_msg,
        )

    def _mutation_error(
        self,
        exc: Exception,
        *,
        folder_id: str | None,
        filename: str | None,
        expected_base_commit: str | None = None,
    ) -> dict:
        if isinstance(exc, CASRejectionError):
            return _make_error(
                "BASE_COMMIT_CONFLICT",
                "repository changed since expected base commit",
                folder_id=folder_id,
                filename=filename,
                expected_revision=expected_base_commit,
                current_commit=self._commit(),
                retryable=True,
            )
        if isinstance(exc, MutationBrokenError):
            return _make_error(
                "BROKEN",
                "governed mutation failed closed; inspect server-side evidence",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if isinstance(exc, PolicyViolationError):
            return _make_error(
                "POLICY_VIOLATION",
                str(exc),
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if isinstance(exc, BatchOpError):
            return _make_error(
                exc.code,
                str(exc),
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if isinstance(exc, ValueError):
            return _make_error(
                "INVALID_CONTENT",
                str(exc),
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        return _make_error(
            "OPERATION_FAILED",
            "operation failed; inspect server-side logs",
            folder_id=folder_id,
            filename=filename,
            current_commit=self._commit(),
        )

    def _check_size(
        self,
        folder_id: str,
        filename: str,
        content: str,
    ) -> dict | None:
        if len(content.encode("utf-8")) <= _MAX_FILE_SIZE:
            return None
        return _make_error(
            "CONTENT_TOO_LARGE",
            f"content exceeds {_MAX_FILE_SIZE} bytes",
            folder_id=folder_id,
            filename=filename,
            current_commit=self._commit(),
        )

    def _check_revision(
        self,
        folder_id: str,
        filename: str,
        internal_name: str,
        expected_revision: str | None,
    ) -> dict | None:
        if expected_revision is None:
            return None
        try:
            if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
                return None
            actual = _content_hash(self._vfs.read_text(internal_name))
        except VFSError:
            return None
        if actual == expected_revision:
            return None
        return _make_error(
            "REVISION_CONFLICT",
            "file changed since expected revision",
            folder_id=folder_id,
            filename=filename,
            expected_revision=expected_revision,
            actual_revision=actual,
            current_commit=self._commit(),
            retryable=True,
        )

    def _check_idempotency(self, idempotency_key: str | None) -> dict | None:
        if idempotency_key is None:
            return None
        for manifest in self._binding.manifest.list_manifests():
            result = manifest.get("result", {})
            if result.get("idempotency_key") == idempotency_key:
                return _make_error(
                    "IDEMPOTENCY_CONFLICT",
                    "idempotency key was already used",
                    current_commit=self._commit(),
                )
        return None

    def _validate_brief(self, folder_id: str, content: str) -> str | None:
        try:
            parsed = parse_brief(content)
        except BriefError as exc:
            return f"content is not a valid brief: {exc}"
        frontmatter = parsed["frontmatter"]
        if frontmatter.get("id") != folder_id:
            return "_brief.md id is immutable and must equal folder_id"
        if frontmatter.get("status") not in VALID_STATUS:
            return f"invalid status: {frontmatter.get('status')}"
        problems = validate_brief(content)
        return "; ".join(problems) if problems else None

    def _check_governed_invariants(
        self,
        folder_id: str,
        filename: str,
        old_content: str,
        new_content: str,
    ) -> dict | None:
        basename = _basename(filename)
        if basename == "progress.md":
            _, old_changelog = _extract_changelog_section(old_content)
            _, new_changelog = _extract_changelog_section(new_content)
            if old_changelog and not new_changelog.startswith(old_changelog):
                return _make_error(
                    "POLICY_VIOLATION",
                    "progress changelog is append-only",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
            if _has_heading(old_content, "## Blocked") and not _has_heading(
                new_content, "## Blocked"
            ):
                return _make_error(
                    "POLICY_VIOLATION",
                    "progress must conserve the Blocked section",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        elif basename == "golden-order.md" and not new_content.startswith(old_content):
            return _make_error(
                "POLICY_VIOLATION",
                "golden-order.md is append-only",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        elif basename in ("CLAUDE.md", "AGENTS.md"):
            required = ("## Goal", "## Status", "## Key Context", "## Resume Steps")
            if any(
                _has_heading(old_content, heading)
                and not _has_heading(new_content, heading)
                for heading in required
            ):
                return _make_error(
                    "POLICY_VIOLATION",
                    "resume guide sections must be conserved",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        elif basename == "context.md":
            required = ("## 工作上下文", "## 关键路径", "## 环境信息")
            if any(
                _has_heading(old_content, heading)
                and not _has_heading(new_content, heading)
                for heading in required
            ):
                return _make_error(
                    "POLICY_VIOLATION",
                    "context sections must be conserved",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        return None

    def _entry(self, folder_id: str, internal_name: str) -> dict:
        filename = internal_name[len(folder_id) + 1 :]
        content = self._vfs.read_text(internal_name)
        stat = self._vfs.stat(internal_name)
        return {
            "filename": filename,
            "node_type": "file",
            "size": stat["size"],
            "content_revision": _content_hash(content),
        }

    def _file_success(
        self,
        folder_id: str,
        filename: str,
        content: str,
        *,
        mutation_result: dict | None = None,
    ) -> dict:
        internal_name = f"{folder_id}/{filename}"
        stat = self._vfs.stat(internal_name)
        return _make_success(
            folder_id=folder_id,
            filename=filename,
            node_type="file",
            size=stat["size"],
            content=content,
            content_revision=_content_hash(content),
            commit=self._commit(),
            git=mutation_result.get("git") if mutation_result else None,
            mutation_id=_mutation_id(mutation_result or {}),
        )

    # -- Discovery ---------------------------------------------------------

    def fs_capabilities(self) -> dict:
        return _make_success(
            commit=self._commit(),
            capabilities={
                "operations": [
                    "fs_capabilities",
                    "fs_resolve",
                    "fs_stat",
                    "fs_list",
                    "fs_read",
                    "fs_create",
                    "fs_write",
                    "fs_edit",
                    "fs_copy",
                    "fs_rename",
                    "fs_delete",
                    "fs_batch",
                ],
                "addressing": "folder_id + folder-relative filename",
                "max_file_size": _MAX_FILE_SIZE,
                "idempotency": (
                    "idempotency_key + expected_base_commit + "
                    "expected_resource_revision"
                ),
            },
        )

    def fs_resolve(self, folder_id: str, filename: str = BRIEF_NAME) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        try:
            if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    "file not found",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
            content = self._vfs.read_text(internal_name)
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to resolve file",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        return self._file_success(folder_id, filename, content)

    def fs_stat(self, folder_id: str, filename: str) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        try:
            stat = self._vfs.stat(internal_name)
            if stat["is_dir"]:
                return _make_success(
                    folder_id=folder_id,
                    filename=filename,
                    node_type="directory",
                    commit=self._commit(),
                )
            content = self._vfs.read_text(internal_name)
        except VFSError:
            return _make_error(
                "RESOURCE_NOT_FOUND",
                "file or directory not found",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        return _make_success(
            folder_id=folder_id,
            filename=filename,
            node_type="file",
            size=stat["size"],
            content_revision=_content_hash(content),
            commit=self._commit(),
        )

    def fs_list(self, folder_id: str, dirname: str = "") -> dict:
        internal_dir, error = self._resolve(folder_id, dirname, allow_empty=True)
        if error:
            return error
        try:
            if not self._vfs.exists(internal_dir) or not self._vfs.is_dir(internal_dir):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    "directory not found",
                    folder_id=folder_id,
                    filename=dirname,
                    current_commit=self._commit(),
                )
            entries = [
                self._entry(folder_id, name)
                for name in self._vfs.ls(internal_dir)
            ]
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to list directory",
                folder_id=folder_id,
                filename=dirname,
                current_commit=self._commit(),
            )
        return _make_success(
            folder_id=folder_id,
            filename=dirname,
            node_type="directory",
            commit=self._commit(),
            entries=entries,
        )

    def fs_read(
        self,
        folder_id: str,
        filename: str,
        offset: int | None = None,
        limit: int | None = None,
    ) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        try:
            if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    "file not found",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
            raw = self._vfs.read_text(internal_name)
            stat = self._vfs.stat(internal_name)
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to read file",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        lines = raw.splitlines()
        start = max(1, offset or 1)
        end = len(lines) if limit is None else min(len(lines), start + max(0, limit) - 1)
        rendered = (
            "\n".join(f"{index}\t{lines[index - 1]}" for index in range(start, end + 1))
            if start <= end
            else ""
        )
        return _make_success(
            folder_id=folder_id,
            filename=filename,
            node_type="file",
            size=stat["size"],
            content=rendered,
            content_revision=_content_hash(raw),
            commit=self._commit(),
        )

    # -- Mutations ---------------------------------------------------------

    def fs_create(
        self,
        folder_id: str,
        filename: str,
        content: str,
        expected_base_commit: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        if _basename(filename) in _CRITICAL_FILES or filename == BRIEF_NAME:
            return _make_error(
                "POLICY_VIOLATION",
                "critical and identity files are created by lifecycle tools",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if size_error := self._check_size(folder_id, filename, content):
            return size_error
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        try:
            if self._vfs.exists(internal_name):
                return _make_error(
                    "RESOURCE_EXISTS",
                    "file already exists",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to create file",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )

        def write(binding, args):
            binding.vfs.write(internal_name, content, op="fs_create", args=args)
            result = {"changed_paths": [internal_name]}
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        args = {"folder_id": folder_id, "filename": filename, "content": content}
        try:
            mutation = self._call_mutate(
                "fs_create",
                args,
                write,
                expected_base_commit,
                f"chore(wf): create {folder_id}:{filename}",
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=folder_id,
                filename=filename,
                expected_base_commit=expected_base_commit,
            )
        return self._file_success(
            folder_id,
            filename,
            self._vfs.read_text(internal_name),
            mutation_result=mutation,
        )

    def fs_write(
        self,
        folder_id: str,
        filename: str,
        content: str,
        expected_base_commit: str | None = None,
        expected_resource_revision: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        if size_error := self._check_size(folder_id, filename, content):
            return size_error
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        if revision_error := self._check_revision(
            folder_id,
            filename,
            internal_name,
            expected_resource_revision,
        ):
            return revision_error
        try:
            if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    "write does not implicitly create files",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
            old_content = self._vfs.read_text(internal_name)
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to write file",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if filename == BRIEF_NAME:
            if brief_error := self._validate_brief(folder_id, content):
                return _make_error(
                    "INVALID_CONTENT",
                    brief_error,
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        if _basename(filename) in _CRITICAL_FILES | _GOVERNED_FILES:
            if invariant_error := self._check_governed_invariants(
                folder_id,
                filename,
                old_content,
                content,
            ):
                return invariant_error

        def write(binding, args):
            binding.vfs.write(internal_name, content, op="fs_write", args=args)
            result = {"changed_paths": [internal_name]}
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        args = {"folder_id": folder_id, "filename": filename, "content": content}
        try:
            mutation = self._call_mutate(
                "fs_write",
                args,
                write,
                expected_base_commit,
                f"chore(wf): write {folder_id}:{filename}",
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=folder_id,
                filename=filename,
                expected_base_commit=expected_base_commit,
            )
        return self._file_success(
            folder_id,
            filename,
            self._vfs.read_text(internal_name),
            mutation_result=mutation,
        )

    def fs_edit(
        self,
        folder_id: str,
        filename: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        expected_base_commit: str | None = None,
        expected_resource_revision: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        if not old_string or old_string == new_string:
            return _make_error(
                "INVALID_CONTENT",
                "old_string must be non-empty and differ from new_string",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        if revision_error := self._check_revision(
            folder_id,
            filename,
            internal_name,
            expected_resource_revision,
        ):
            return revision_error
        try:
            if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    "file not found",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
            old_content = self._vfs.read_text(internal_name)
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to edit file",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        count = old_content.count(old_string)
        if count == 0 or (count > 1 and not replace_all):
            return _make_error(
                "INVALID_CONTENT",
                (
                    "old_string was not found"
                    if count == 0
                    else f"old_string matches {count} times; narrow it or set replace_all"
                ),
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        content = (
            old_content.replace(old_string, new_string)
            if replace_all
            else old_content.replace(old_string, new_string, 1)
        )
        if size_error := self._check_size(folder_id, filename, content):
            return size_error
        if filename == BRIEF_NAME:
            if brief_error := self._validate_brief(folder_id, content):
                return _make_error(
                    "INVALID_CONTENT",
                    brief_error,
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        if _basename(filename) in _CRITICAL_FILES | _GOVERNED_FILES:
            if invariant_error := self._check_governed_invariants(
                folder_id,
                filename,
                old_content,
                content,
            ):
                return invariant_error

        def write(binding, args):
            binding.vfs.write(internal_name, content, op="fs_edit", args=args)
            result = {"changed_paths": [internal_name]}
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        args = {
            "folder_id": folder_id,
            "filename": filename,
            "old_string": old_string,
            "new_string": new_string,
            "content": content,
        }
        try:
            mutation = self._call_mutate(
                "fs_edit",
                args,
                write,
                expected_base_commit,
                f"chore(wf): edit {folder_id}:{filename}",
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=folder_id,
                filename=filename,
                expected_base_commit=expected_base_commit,
            )
        return self._file_success(
            folder_id,
            filename,
            self._vfs.read_text(internal_name),
            mutation_result=mutation,
        )

    def _resolve_transfer(
        self,
        source_folder_id: str,
        source_filename: str,
        dest_folder_id: str,
        dest_filename: str,
    ) -> tuple[str | None, str | None, dict | None]:
        source, error = self._resolve(source_folder_id, source_filename)
        if error:
            return None, None, error
        dest, error = self._resolve(dest_folder_id, dest_filename)
        if error:
            return None, None, error
        for folder_id, filename in (
            (source_folder_id, source_filename),
            (dest_folder_id, dest_filename),
        ):
            if filename == BRIEF_NAME or _basename(filename) in _CRITICAL_FILES:
                return None, None, _make_error(
                    "POLICY_VIOLATION",
                    "identity and critical files cannot be copied or renamed",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        try:
            if not self._vfs.exists(source) or not self._vfs.is_file(source):
                return None, None, _make_error(
                    "RESOURCE_NOT_FOUND",
                    "source file not found",
                    folder_id=source_folder_id,
                    filename=source_filename,
                    current_commit=self._commit(),
                )
            if self._vfs.exists(dest):
                return None, None, _make_error(
                    "RESOURCE_EXISTS",
                    "destination already exists",
                    folder_id=dest_folder_id,
                    filename=dest_filename,
                    current_commit=self._commit(),
                )
        except VFSError:
            return None, None, _make_error(
                "INVALID_PATH",
                "unable to resolve transfer",
                folder_id=source_folder_id,
                filename=source_filename,
                current_commit=self._commit(),
            )
        return source, dest, None

    def fs_copy(
        self,
        source_folder_id: str,
        source_filename: str,
        dest_folder_id: str,
        dest_filename: str,
        expected_base_commit: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        source, dest, error = self._resolve_transfer(
            source_folder_id,
            source_filename,
            dest_folder_id,
            dest_filename,
        )
        if error:
            return error
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        content = self._vfs.read_text(source)

        def write(binding, args):
            binding.vfs.write(dest, content, op="fs_copy", args=args)
            result = {"changed_paths": [dest]}
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        args = {
            "source_folder_id": source_folder_id,
            "source_filename": source_filename,
            "dest_folder_id": dest_folder_id,
            "dest_filename": dest_filename,
        }
        try:
            mutation = self._call_mutate(
                "fs_copy",
                args,
                write,
                expected_base_commit,
                (
                    f"chore(wf): copy {source_folder_id}:{source_filename} -> "
                    f"{dest_folder_id}:{dest_filename}"
                ),
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=source_folder_id,
                filename=source_filename,
                expected_base_commit=expected_base_commit,
            )
        return self._file_success(
            dest_folder_id,
            dest_filename,
            self._vfs.read_text(dest),
            mutation_result=mutation,
        )

    def fs_rename(
        self,
        source_folder_id: str,
        source_filename: str,
        dest_folder_id: str,
        dest_filename: str,
        expected_base_commit: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        source, dest, error = self._resolve_transfer(
            source_folder_id,
            source_filename,
            dest_folder_id,
            dest_filename,
        )
        if error:
            return error
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        content = self._vfs.read_text(source)

        def write(binding, args):
            binding.vfs.write(dest, content, op="fs_rename", args=args)
            binding.vfs.delete(source, op="fs_rename", args=args)
            result = {"changed_paths": [source, dest]}
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        args = {
            "source_folder_id": source_folder_id,
            "source_filename": source_filename,
            "dest_folder_id": dest_folder_id,
            "dest_filename": dest_filename,
        }
        try:
            mutation = self._call_mutate(
                "fs_rename",
                args,
                write,
                expected_base_commit,
                (
                    f"chore(wf): rename {source_folder_id}:{source_filename} -> "
                    f"{dest_folder_id}:{dest_filename}"
                ),
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=source_folder_id,
                filename=source_filename,
                expected_base_commit=expected_base_commit,
            )
        return self._file_success(
            dest_folder_id,
            dest_filename,
            self._vfs.read_text(dest),
            mutation_result=mutation,
        )

    def fs_delete(
        self,
        folder_id: str,
        filename: str,
        expected_base_commit: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            return error
        if filename == BRIEF_NAME or _basename(filename) in _CRITICAL_FILES:
            return _make_error(
                "POLICY_VIOLATION",
                "identity and critical files are deleted by lifecycle tools",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        try:
            if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
                return _make_error(
                    "RESOURCE_NOT_FOUND",
                    "file not found",
                    folder_id=folder_id,
                    filename=filename,
                    current_commit=self._commit(),
                )
        except VFSError:
            return _make_error(
                "INVALID_PATH",
                "unable to delete file",
                folder_id=folder_id,
                filename=filename,
                current_commit=self._commit(),
            )

        def write(binding, args):
            binding.vfs.delete(internal_name, op="delete", args=args)
            result = {"changed_paths": [internal_name]}
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        args = {"folder_id": folder_id, "filename": filename}
        try:
            mutation = self._call_mutate(
                "delete",
                args,
                write,
                expected_base_commit,
                f"chore(wf): delete {folder_id}:{filename}",
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=folder_id,
                filename=filename,
                expected_base_commit=expected_base_commit,
            )
        return _make_success(
            folder_id=folder_id,
            filename=filename,
            node_type="file",
            commit=self._commit(),
            git=mutation.get("git"),
            mutation_id=_mutation_id(mutation),
        )

    # -- Batch -------------------------------------------------------------

    def _prepare_batch_op(self, spec: dict, index: int) -> dict:
        op = spec.get("op")
        args = spec.get("args")
        if op not in {
            "fs_create",
            "fs_write",
            "fs_edit",
            "fs_copy",
            "fs_rename",
            "fs_delete",
        } or not isinstance(args, dict):
            raise BatchOpError("INVALID_CONTENT", f"batch op {index}: invalid operation")

        prepared = {"op": op, "args": args}
        if op in {"fs_copy", "fs_rename"}:
            source, dest, error = self._resolve_transfer(
                args.get("source_folder_id", ""),
                args.get("source_filename", ""),
                args.get("dest_folder_id", ""),
                args.get("dest_filename", ""),
            )
            if error:
                raise BatchOpError(error["code"], f"batch op {index}: {error['message']}")
            prepared.update({
                "source": source,
                "dest": dest,
                "content": self._vfs.read_text(source),
            })
            return prepared

        folder_id = args.get("folder_id", "")
        filename = args.get("filename", "")
        internal_name, error = self._resolve(folder_id, filename)
        if error:
            raise BatchOpError(error["code"], f"batch op {index}: {error['message']}")
        prepared["internal_name"] = internal_name
        if op == "fs_create":
            if filename == BRIEF_NAME or _basename(filename) in _CRITICAL_FILES:
                raise BatchOpError(
                    "POLICY_VIOLATION",
                    f"batch op {index}: lifecycle-managed file",
                )
            if self._vfs.exists(internal_name):
                raise BatchOpError(
                    "RESOURCE_EXISTS",
                    f"batch op {index}: destination exists",
                )
            content = args.get("content", "")
            if self._check_size(folder_id, filename, content):
                raise BatchOpError(
                    "CONTENT_TOO_LARGE",
                    f"batch op {index}: content too large",
                )
            prepared["content"] = content
            return prepared

        if not self._vfs.exists(internal_name) or not self._vfs.is_file(internal_name):
            raise BatchOpError(
                "RESOURCE_NOT_FOUND",
                f"batch op {index}: file not found",
            )
        old_content = self._vfs.read_text(internal_name)
        prepared["old_content"] = old_content

        if op == "fs_delete":
            if filename == BRIEF_NAME or _basename(filename) in _CRITICAL_FILES:
                raise BatchOpError(
                    "POLICY_VIOLATION",
                    f"batch op {index}: lifecycle-managed file",
                )
            return prepared

        expected_revision = args.get("expected_resource_revision")
        if expected_revision and _content_hash(old_content) != expected_revision:
            raise BatchOpError(
                "REVISION_CONFLICT",
                f"batch op {index}: revision conflict",
            )

        if op == "fs_write":
            content = args.get("content", "")
        else:
            old_string = args.get("old_string", "")
            new_string = args.get("new_string", "")
            count = old_content.count(old_string)
            if (
                not old_string
                or old_string == new_string
                or count == 0
                or (count > 1 and not args.get("replace_all", False))
            ):
                raise BatchOpError(
                    "INVALID_CONTENT",
                    f"batch op {index}: invalid edit match",
                )
            content = (
                old_content.replace(old_string, new_string)
                if args.get("replace_all", False)
                else old_content.replace(old_string, new_string, 1)
            )
        if self._check_size(folder_id, filename, content):
            raise BatchOpError(
                "CONTENT_TOO_LARGE",
                f"batch op {index}: content too large",
            )
        if filename == BRIEF_NAME:
            brief_error = self._validate_brief(folder_id, content)
            if brief_error:
                raise BatchOpError(
                    "INVALID_CONTENT",
                    f"batch op {index}: {brief_error}",
                )
        if _basename(filename) in _CRITICAL_FILES | _GOVERNED_FILES:
            invariant_error = self._check_governed_invariants(
                folder_id,
                filename,
                old_content,
                content,
            )
            if invariant_error:
                raise BatchOpError(
                    invariant_error["code"],
                    f"batch op {index}: {invariant_error['message']}",
                )
        prepared["content"] = content
        return prepared

    def fs_batch(
        self,
        operations: list[dict],
        expected_base_commit: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        if not operations:
            return _make_error(
                "INVALID_CONTENT",
                "batch operations list is empty",
                current_commit=self._commit(),
            )
        if idem_error := self._check_idempotency(idempotency_key):
            return idem_error
        try:
            prepared = [
                self._prepare_batch_op(spec, index)
                for index, spec in enumerate(operations)
            ]
        except BatchOpError as exc:
            return self._mutation_error(exc, folder_id=None, filename=None)

        def write(binding, args):
            changed: list[str] = []
            safe_results: list[dict] = []
            for item in prepared:
                op = item["op"]
                op_args = item["args"]
                if op == "fs_create":
                    binding.vfs.write(
                        item["internal_name"],
                        item["content"],
                        op="fs_batch",
                        args=args,
                    )
                    changed.append(item["internal_name"])
                    safe_results.append({
                        "op": op,
                        "folder_id": op_args["folder_id"],
                        "filename": op_args["filename"],
                    })
                elif op in {"fs_write", "fs_edit"}:
                    binding.vfs.write(
                        item["internal_name"],
                        item["content"],
                        op="fs_batch",
                        args=args,
                    )
                    changed.append(item["internal_name"])
                    safe_results.append({
                        "op": op,
                        "folder_id": op_args["folder_id"],
                        "filename": op_args["filename"],
                    })
                elif op == "fs_copy":
                    binding.vfs.write(
                        item["dest"],
                        item["content"],
                        op="fs_batch",
                        args=args,
                    )
                    changed.append(item["dest"])
                    safe_results.append({
                        "op": op,
                        "folder_id": op_args["dest_folder_id"],
                        "filename": op_args["dest_filename"],
                    })
                elif op == "fs_rename":
                    binding.vfs.write(
                        item["dest"],
                        item["content"],
                        op="fs_batch",
                        args=args,
                    )
                    binding.vfs.delete(item["source"], op="fs_batch", args=args)
                    changed.extend([item["source"], item["dest"]])
                    safe_results.append({
                        "op": op,
                        "folder_id": op_args["dest_folder_id"],
                        "filename": op_args["dest_filename"],
                    })
                elif op == "fs_delete":
                    binding.vfs.delete(
                        item["internal_name"],
                        op="fs_batch",
                        args=args,
                    )
                    changed.append(item["internal_name"])
                    safe_results.append({
                        "op": op,
                        "folder_id": op_args["folder_id"],
                        "filename": op_args["filename"],
                    })
            result = {
                "changed_paths": changed,
                "batch_results": safe_results,
            }
            if idempotency_key:
                result["idempotency_key"] = idempotency_key
            return result

        try:
            mutation = self._call_mutate(
                "fs_batch",
                {"operations": operations},
                write,
                expected_base_commit,
                "chore(wf): batch",
            )
        except Exception as exc:
            return self._mutation_error(
                exc,
                folder_id=None,
                filename=None,
                expected_base_commit=expected_base_commit,
            )
        return _make_success(
            node_type="batch",
            commit=self._commit(),
            batch_results=mutation.get("batch_results", []),
            git=mutation.get("git"),
            mutation_id=_mutation_id(mutation),
        )
