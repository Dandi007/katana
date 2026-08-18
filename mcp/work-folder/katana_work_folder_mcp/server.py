"""katana-work-folder-mcp — flat, ID-only Work Folder MCP server。

数据仓根即 Work Folder 根；每个 folder 的唯一物理位置为 ``wf-ID/``。公共
tool 只接受 opaque ``folder_id`` 与 folder-relative filename，不返回物理路径。
所有 mutation 仍经 GovernedKernel 完成 CAS、policy、manifest 与 Git commit。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from fastmcp import FastMCP

from katana_kb_mcp_shared import config, vault_search
from katana_kernel import (
    CASRejectionError,
    DirtyWorkTreeError,
    GovernedKernel,
    GovernedVFS,
    IdempotencyConflictError,
    MutationBrokenError,
    ResourceIdLedger,
    SQLiteMutationLedger,
    TransactionManifest,
    require_exact_git_root,
)
from katana_work_folder_mcp import lifecycle as _lifecycle
from katana_work_folder_mcp.fs_tools import FSTools, ID_RE
from katana_work_folder_mcp.store import (
    WorkFolderStore,
    _render_index_snapshot,
    _wf_policy,
)

mcp = FastMCP(
    "katana-work-folder-mcp",
    instructions=(
        "Work Folder 持久化接口。所有寻址使用 folder_id 与 folder-relative "
        "filename；返回值不暴露物理路径。"
    ),
)

_repo_root: str | None = None
_kernel: GovernedKernel | None = None
_store: WorkFolderStore | None = None
_fs_tools: FSTools | None = None

_LAYOUT_CANARY = ".katana/flat-layout.json"
_ALLOWED_ROOT_FILES = {
    ".gitignore",
    ".gitkeep",
    "INDEX.md",
}
_ALLOWED_KATANA_ENTRIES = {
    "control-archive",
    "flat-layout.json",
    "legacy-manifest-inventory.json",
    "legacy-manifests",
    "runtime",
    "tombstones.json",
}
_SEARCH_OVERSAMPLE_FACTOR = 4
_SEARCH_MIN_CANDIDATES = 20

_DROP_PUBLIC_KEYS = {
    "changed_paths",
    "commit_msg",
    "folder",
    "id",
    "idempotency_key",
    "index_path",
    "manifest",
    "name",
    "path",
    "paths",
    "request_fingerprint",
    "resource_id",
    "rollback",
    "tombstoned_ids",
    "virtual_path",
}

_RESUME_FIELD_KEYS = {
    "goal",
    "phase",
    "status",
    "key_context",
    "decisions",
    "issues",
    "lessons",
}


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _safe_resume_fields(fields: dict | None) -> dict | None:
    """只保留 resume guide 的语义字段；folder identity 由 server 注入。"""
    if not fields:
        return None
    return {key: value for key, value in fields.items() if key in _RESUME_FIELD_KEYS}


def _redact_string(value: str) -> str:
    if _repo_root:
        return value.replace(os.path.realpath(_repo_root), "<work-folder-root>")
    return value


def _public_payload(value: Any) -> Any:
    """移除 kernel/internal locator 字段，并递归遮蔽物理 repo root。"""
    if isinstance(value, dict):
        mutation_id = None
        manifest = value.get("manifest")
        if isinstance(manifest, dict):
            mutation_id = manifest.get("manifest_id")
        clean = {
            key: _public_payload(child)
            for key, child in value.items()
            if key not in _DROP_PUBLIC_KEYS
        }
        if mutation_id and "mutation_id" not in clean:
            clean["mutation_id"] = mutation_id
        return clean
    if isinstance(value, list):
        return [_public_payload(child) for child in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _server_mutation(call, *, reconcile: bool = False) -> dict:
    try:
        return _public_payload(call())
    except IdempotencyConflictError as exc:
        return {
            "ok": False,
            "code": "IDEMPOTENCY_CONFLICT",
            "message": str(exc),
            "retryable": False,
        }
    except DirtyWorkTreeError as exc:
        return {
            "ok": False,
            "code": "WORKTREE_DIRTY",
            "message": _redact_string(str(exc)),
            "retryable": True,
        }
    except CASRejectionError as exc:
        return {
            "ok": False,
            "code": "BASE_COMMIT_CONFLICT",
            "message": _redact_string(str(exc)),
            "retryable": True,
        }
    except MutationBrokenError as exc:
        if reconcile:
            return _reconcile_broken_envelope(exc)
        return _public_payload(exc.as_error())


def _require_git_root(repo_root: str) -> str:
    """验证 repo_root 已是 exact Git root；绝不隐式初始化。"""
    try:
        return require_exact_git_root(repo_root)
    except ValueError as exc:
        raise ValueError(f"invalid work-folder Git repository root: {exc}") from exc


def _reject_nested_git_metadata(repo: Path) -> None:
    for child in repo.iterdir():
        if child.name == ".git":
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        for candidate in child.rglob(".git"):
            relative = candidate.relative_to(repo).as_posix()
            raise ValueError(
                f"flat topology rejects nested Git metadata: {relative}"
            )


def _validate_flat_topology(root: str) -> None:
    """Reject every pre-cutover or ambiguous repository topology."""
    repo = Path(root)
    _reject_nested_git_metadata(repo)
    canary = repo / _LAYOUT_CANARY
    try:
        canary_payload = json.loads(canary.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("flat-layout migration canary is missing or invalid") from exc
    if canary_payload != {"layout": "flat-id-v1", "schema_version": 1}:
        raise ValueError("flat-layout migration canary has an unsupported schema")

    tombstones = repo / ".katana" / "tombstones.json"
    if tombstones.is_symlink() or not tombstones.is_file():
        raise ValueError("flat repository requires a regular tombstone ledger")
    legacy_manifests = repo / ".katana" / "manifests"
    if legacy_manifests.exists() or legacy_manifests.is_symlink():
        raise ValueError(
            "legacy manifest directory must be archived before server startup"
        )

    for entry in repo.iterdir():
        if entry.is_symlink():
            raise ValueError(f"flat topology rejects symlink: {entry.name}")
        if entry.is_file():
            if entry.name not in _ALLOWED_ROOT_FILES:
                raise ValueError(
                    f"flat topology contains unknown root payload: {entry.name}"
                )
            continue
        if not entry.is_dir():
            raise ValueError(
                f"flat topology contains special root payload: {entry.name}"
            )
        if entry.name in {".git", ".katana"}:
            continue
        if not ID_RE.fullmatch(entry.name):
            raise ValueError(
                f"legacy or ambiguous work-folder topology remains: {entry.name}"
            )
        brief = entry / "_brief.md"
        if brief.is_symlink() or not brief.is_file():
            raise ValueError(
                f"flat work folder is missing a regular _brief.md: {entry.name}"
            )
        for descendant in entry.rglob("*"):
            relative_parts = descendant.relative_to(entry).parts
            if any(part in {".git", ".katana"} for part in relative_parts):
                relative = descendant.relative_to(repo).as_posix()
                raise ValueError(
                    f"flat topology contains reserved topic metadata: {relative}"
                )
            if descendant.is_symlink():
                relative = descendant.relative_to(repo).as_posix()
                raise ValueError(f"flat topology rejects symlink: {relative}")

    katana = repo / ".katana"
    for entry in katana.iterdir():
        if entry.name not in _ALLOWED_KATANA_ENTRIES:
            raise ValueError(
                f"flat topology contains unknown .katana control: {entry.name}"
            )
        if entry.is_symlink():
            raise ValueError(
                f"flat topology rejects .katana symlink: {entry.name}"
            )
        if entry.is_dir():
            for descendant in entry.rglob("*"):
                if descendant.is_symlink():
                    relative = descendant.relative_to(repo).as_posix()
                    raise ValueError(
                        f"flat topology rejects .katana symlink: {relative}"
                    )

    _validate_legacy_manifest_inventory(repo)


def _safe_repo_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _validate_legacy_manifest_inventory(repo: Path) -> None:
    """Prove every pre-cutover manifest is archived and inventoried."""

    inventory_path = repo / ".katana/legacy-manifest-inventory.json"
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "legacy manifest inventory is missing or invalid"
        ) from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("manifests"), list)
    ):
        raise ValueError("legacy manifest inventory has an unsupported schema")

    expected: dict[str, dict] = {}
    for record in payload["manifests"]:
        if not isinstance(record, dict):
            raise ValueError("legacy manifest inventory entry is invalid")
        source = record.get("source_repo_path")
        archive = record.get("archive_repo_path")
        digest = record.get("sha256")
        size = record.get("size")
        if (
            not _safe_repo_relative(source)
            or not _safe_repo_relative(archive)
            or not archive.startswith(".katana/legacy-manifests/")
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or type(size) is not int
            or size < 0
            or type(record.get("git_tracked")) is not bool
            or archive in expected
        ):
            raise ValueError("legacy manifest inventory entry is invalid")
        expected[archive] = record

    archive_root = repo / ".katana/legacy-manifests"
    actual: set[str] = set()
    if archive_root.exists():
        if archive_root.is_symlink() or not archive_root.is_dir():
            raise ValueError("legacy manifest archive must be a real directory")
        for item in archive_root.rglob("*"):
            relative = item.relative_to(repo).as_posix()
            if item.is_symlink() or (not item.is_dir() and not item.is_file()):
                raise ValueError(
                    f"legacy manifest archive contains unsafe payload: {relative}"
                )
            if item.is_file():
                actual.add(relative)
    if actual != set(expected):
        raise ValueError(
            "legacy manifest inventory does not match archived files"
        )
    for archive, record in expected.items():
        content = (repo / archive).read_bytes()
        if (
            len(content) != record["size"]
            or hashlib.sha256(content).hexdigest() != record["sha256"]
        ):
            raise ValueError(
                f"legacy manifest archive hash mismatch: {archive}"
            )


def _validate_flat_index(kernel: GovernedKernel, root: str) -> None:
    binding = kernel.get_binding("work-folder")
    rendered, indexed, skipped, errors = _render_index_snapshot(binding)
    index_path = Path(root) / "INDEX.md"
    try:
        current = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("flat repository INDEX.md is missing or unreadable") from exc
    live_ids = {
        entry.name
        for entry in Path(root).iterdir()
        if entry.is_dir() and ID_RE.fullmatch(entry.name)
    }
    overlap = sorted(live_ids & binding.ledger.tombstones)
    if overlap:
        raise ValueError(
            f"live folder IDs overlap tombstones: {', '.join(overlap)}"
        )
    if errors or skipped or indexed != len(live_ids):
        raise ValueError(
            "flat repository INDEX inputs are incomplete or invalid"
        )
    if current != rendered:
        raise ValueError("flat repository INDEX.md is stale")


_APP_LOGGER_NAME = "katana_work_folder_mcp"


def _configure_logging() -> None:
    """Raise the app logger to INFO on stderr so mutation lines reach journald.

    The app logger has no level of its own by default and inherits root's
    WARNING default with no dedicated handler, which silently drops every
    ``log_mutation`` INFO record before it is ever formatted. ``configure`` is
    the single service entry point shared by ``main`` and ``build_remote_app``,
    so installing the handler here covers the real service; keep it idempotent
    because tests and repeated callers may invoke ``configure`` more than once.
    """
    logger = logging.getLogger(_APP_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not any(isinstance(handler, logging.StreamHandler) for handler in logger.handlers):
        logger.addHandler(logging.StreamHandler(sys.stderr))


def configure(repo_root: str) -> None:
    """将 MCP 绑定到单一 existing Git data root。"""
    global _repo_root, _kernel, _store, _fs_tools
    _configure_logging()
    root = _require_git_root(repo_root)
    _validate_flat_topology(root)

    kernel = GovernedKernel()
    vfs = GovernedVFS(root)
    ledger = ResourceIdLedger(
        os.path.join(root, ".katana", "tombstones.json"),
        prefix="wf-",
    )
    runtime_root = os.path.join(root, ".katana", "runtime")
    manifest = TransactionManifest(
        os.path.join(runtime_root, "manifests"),
        git_tracked=False,
    )
    mutation_ledger = SQLiteMutationLedger(
        os.path.join(runtime_root, "mutations.sqlite"),
    )
    kernel.bind(
        "work-folder",
        _wf_policy(),
        vfs,
        ledger,
        manifest,
        root,
        mutation_ledger=mutation_ledger,
        runtime_state_paths=[os.path.join(runtime_root, "evidence")],
    )
    _validate_flat_index(kernel, root)
    kernel.reconcile("work-folder")

    _repo_root = root
    _kernel = kernel
    _store = WorkFolderStore(kernel)
    _fs_tools = FSTools(kernel, root)


def _require_store() -> WorkFolderStore:
    if _store is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _store


def _require_fs_tools() -> FSTools:
    if _fs_tools is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _fs_tools


def _require_kernel() -> GovernedKernel:
    if _kernel is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _kernel


def _reconcile_broken_envelope(exc: MutationBrokenError) -> dict:
    """Return a redacted, structured BROKEN diagnosis for ``wf_reconcile``.

    Physical repo roots are masked, but the mutation id, affected repo-relative
    paths, and copy-paste recovery commands are preserved for an operator.
    """
    envelope = exc.as_error()
    rollback = envelope.get("rollback") or {}
    return {
        "ok": False,
        "code": "BROKEN",
        "message": envelope.get("message"),
        "manual_recovery_required": True,
        "mutation_id": rollback.get("mutation_id"),
        "diagnostics": {
            "detail": _redact_string(str(rollback.get("detail") or "")),
            "paths": [
                _redact_string(str(path)) for path in (rollback.get("paths") or [])
            ],
            "suggested_commands": [
                _redact_string(str(command))
                for command in (rollback.get("suggested_commands") or [])
            ],
        },
    }


def _do_search(query: str, top_k: int) -> list[dict]:
    """先限定 Work Folder source，再把 locator 收敛为 ID + filename。"""
    if _repo_root is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    if top_k <= 0:
        return []
    source_id = hashlib.sha256(_repo_root.encode("utf-8")).hexdigest()
    candidate_top_k = max(
        top_k * _SEARCH_OVERSAMPLE_FACTOR,
        _SEARCH_MIN_CANDIDATES,
    )
    response = vault_search.search(
        query,
        top_k=candidate_top_k,
        source_root=_repo_root,
        source_id=source_id,
    )
    results: list[dict] = []
    for hit in response.results:
        # Backend source filter 是主隔离边界；返回前仍校验 locator，形成纵深防御。
        locator = str(hit.path)
        if not _safe_repo_relative(locator):
            continue
        folder_id, separator, filename = locator.partition("/")
        if not separator or not filename or not ID_RE.fullmatch(folder_id):
            continue
        results.append({
            "folder_id": folder_id,
            "filename": filename,
            "score": hit.score,
            "title": hit.title,
            "snippet": _redact_string(hit.snippet),
        })
        if len(results) >= top_k:
            break
    return results


@mcp.tool()
async def wf_search(query: str, top_k: int = 10) -> list[dict]:
    """搜索 Work Folder，返回 ``folder_id`` 与 folder-relative filename。"""
    return _do_search(query, top_k)


@mcp.tool()
async def wf_create(
    topic: str,
    expected_base_sha: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """在 data root 直接创建新的 ``wf-ID/`` folder。"""
    return _server_mutation(
        lambda: _require_store().create(
            topic,
            now_fn=_now,
            expected_base_sha=expected_base_sha,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def wf_list(limit: int = 10) -> dict:
    """列出 active folders，只返回语义 metadata 与 folder ID。"""
    if _repo_root is None:
        raise RuntimeError("work-folder store not initialized; call configure() first")
    return _public_payload(_lifecycle.do_list(_repo_root, limit=limit))


@mcp.tool()
async def wf_save(
    folder_id: str,
    summary: str = "checkpoint",
    context_snapshot: str | None = None,
    resume_fields: dict | None = None,
    golden_order_additions: str | None = None,
    findings_addition: str | None = None,
    expected_base_sha: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """用 opaque folder ID 保存 checkpoint。"""
    return _server_mutation(
        lambda: _require_store().save(
            folder_id,
            now_fn=_now,
            summary=summary,
            context_snapshot=context_snapshot,
            resume_fields=_safe_resume_fields(resume_fields),
            golden_order_additions=golden_order_additions,
            findings_addition=findings_addition,
            expected_base_sha=expected_base_sha,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def wf_resume(
    folder_id: str,
    expected_base_sha: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """用 opaque folder ID 恢复并验证工作状态。"""
    return _server_mutation(
        lambda: _require_store().resume(
            folder_id,
            now_fn=_now,
            expected_base_sha=expected_base_sha,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def wf_append_progress(
    folder_id: str,
    entry: str,
    source_session_id: str,
    idempotency_key: str,
    expected_base_sha: str | None = None,
) -> dict:
    """幂等追加 session 进展，并原子更新 brief 与顶层 INDEX。"""
    return _server_mutation(
        lambda: _require_store().append_progress(
            folder_id,
            entry,
            source_session_id,
            idempotency_key,
            now_fn=_now,
            expected_base_sha=expected_base_sha,
        )
    )


@mcp.tool()
async def wf_reindex(
    dry_run: bool = False,
    expected_base_sha: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """重建无 locator 列的顶层 INDEX.md。"""
    return _server_mutation(
        lambda: _require_store().reindex(
            dry_run=dry_run,
            expected_base_sha=expected_base_sha,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def wf_reconcile(
    scope_prefixes: list[str] | None = None,
    control_paths: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """执行 governed 状态的安全恢复清单，幂等；类型 6 保留 BROKEN 不动树。"""
    return _server_mutation(
        lambda: _require_kernel().reconcile(
            "work-folder",
            recover=True,
            scope_prefixes=scope_prefixes,
            control_paths=control_paths,
            idempotency_key=idempotency_key,
        ),
        reconcile=True,
    )


@mcp.tool()
async def fs_capabilities() -> dict:
    return _public_payload(_require_fs_tools().fs_capabilities())


@mcp.tool()
async def fs_resolve(folder_id: str, filename: str = "_brief.md") -> dict:
    return _public_payload(_require_fs_tools().fs_resolve(folder_id, filename))


@mcp.tool()
async def fs_stat(folder_id: str, filename: str) -> dict:
    return _public_payload(_require_fs_tools().fs_stat(folder_id, filename))


@mcp.tool()
async def fs_list(folder_id: str, dirname: str = "") -> dict:
    return _public_payload(_require_fs_tools().fs_list(folder_id, dirname))


@mcp.tool()
async def fs_read(
    folder_id: str,
    filename: str,
    offset: int | None = None,
    limit: int | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_read(
            folder_id,
            filename,
            offset=offset,
            limit=limit,
        )
    )


@mcp.tool()
async def fs_read_bytes(
    folder_id: str,
    filename: str,
    offset: int = 0,
    limit: int = 262_144,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_read_bytes(
            folder_id,
            filename,
            offset=offset,
            limit=limit,
        )
    )


@mcp.tool()
async def fs_create(
    folder_id: str,
    filename: str,
    content: str,
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_create(
            folder_id,
            filename,
            content,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def fs_write(
    folder_id: str,
    filename: str,
    content: str,
    expected_base_commit: str | None = None,
    expected_resource_revision: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_write(
            folder_id,
            filename,
            content,
            expected_base_commit=expected_base_commit,
            expected_resource_revision=expected_resource_revision,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def fs_edit(
    folder_id: str,
    filename: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    expected_base_commit: str | None = None,
    expected_resource_revision: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_edit(
            folder_id,
            filename,
            old_string,
            new_string,
            replace_all=replace_all,
            expected_base_commit=expected_base_commit,
            expected_resource_revision=expected_resource_revision,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def fs_copy(
    source_folder_id: str,
    source_filename: str,
    dest_folder_id: str,
    dest_filename: str,
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_copy(
            source_folder_id,
            source_filename,
            dest_folder_id,
            dest_filename,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def fs_rename(
    source_folder_id: str,
    source_filename: str,
    dest_folder_id: str,
    dest_filename: str,
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_rename(
            source_folder_id,
            source_filename,
            dest_folder_id,
            dest_filename,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def fs_delete(
    folder_id: str,
    filename: str,
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_delete(
            folder_id,
            filename,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def fs_batch(
    operations: list[dict],
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    return _public_payload(
        _require_fs_tools().fs_batch(
            operations,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def wf_evidence_put(
    folder_id: str,
    filename: str,
    content: str,
    conclusion: str | None = None,
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """将高频/大体积/可重生证据产物写入 runtime root，folder 内只留引用。"""
    return _public_payload(
        _require_fs_tools().wf_evidence_put(
            folder_id,
            filename,
            content,
            conclusion=conclusion,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


@mcp.tool()
async def wf_evidence_migrate(
    folder_id: str,
    dry_run: bool = False,
    expected_base_commit: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """一次性把既存 audit-evidence*.md 移交到 runtime root 并留引用指针。"""
    return _public_payload(
        _require_fs_tools().wf_evidence_migrate(
            folder_id,
            dry_run=dry_run,
            expected_base_commit=expected_base_commit,
            idempotency_key=idempotency_key,
        )
    )


def build_remote_app(
    repo_root: str,
    credential_registry,
    *,
    rate_limiter=None,
    readiness_service=None,
    audit_logger=None,
    tenant_resolver=None,
):
    """Build the Work Folder app with remote auth middleware applied."""
    from katana_remote import AuditLogger, RateLimiter, ReadinessService, create_remote_app

    configure(repo_root)
    inner = mcp.http_app()
    rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
    readiness_service = (
        readiness_service if readiness_service is not None else ReadinessService()
    )
    audit_logger = audit_logger if audit_logger is not None else AuditLogger()
    return create_remote_app(
        inner,
        credential_registry=credential_registry,
        rate_limiter=rate_limiter,
        readiness_service=readiness_service,
        audit_logger=audit_logger,
        tenant_resolver=tenant_resolver,
        domain="work-folder",
    )


def main() -> None:
    repo_root = config.kb_root()
    configure(repo_root)
    host = os.environ.get("KATANA_WORK_FOLDER_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("KATANA_WORK_FOLDER_MCP_PORT", "5602"))
    mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
