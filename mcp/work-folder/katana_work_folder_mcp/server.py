"""katana-work-folder-mcp — flat, ID-only Work Folder MCP server。

数据仓根即 Work Folder 根；每个 folder 的唯一物理位置为 ``wf-ID/``。公共
tool 只接受 opaque ``folder_id`` 与 folder-relative filename，不返回物理路径。
所有 mutation 仍经 GovernedKernel 完成 CAS、policy、manifest 与 Git commit。
"""

from __future__ import annotations

import datetime
import os
import subprocess
from typing import Any

from fastmcp import FastMCP

from katana_kb_mcp_shared import config, vault_search
from katana_kernel import (
    GovernedKernel,
    GovernedVFS,
    MutationBrokenError,
    ResourceIdLedger,
    TransactionManifest,
)
from katana_work_folder_mcp import lifecycle as _lifecycle
from katana_work_folder_mcp.fs_tools import FSTools, ID_RE
from katana_work_folder_mcp.store import WorkFolderStore, _wf_policy

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
    "request_fingerprint",
    "resource_id",
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


def _server_mutation(call) -> dict:
    try:
        return _public_payload(call())
    except MutationBrokenError as exc:
        return _public_payload(exc.as_error())


def _require_git_root(repo_root: str) -> str:
    """验证 repo_root 已是 exact Git root；绝不隐式初始化。"""
    root = os.path.realpath(repo_root)
    if not os.path.isdir(root):
        raise ValueError("work-folder repo root does not exist")
    probe = subprocess.run(
        ["git", "-C", root, "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise ValueError("work-folder repo root must be an existing Git repository")
    discovered = os.path.realpath(probe.stdout.strip())
    if discovered != root:
        raise ValueError("configured directory must be the Git repository root")
    return root


def configure(repo_root: str) -> None:
    """将 MCP 绑定到单一 existing Git data root。"""
    global _repo_root, _kernel, _store, _fs_tools
    root = _require_git_root(repo_root)
    _repo_root = root

    kernel = GovernedKernel()
    vfs = GovernedVFS(root)
    ledger = ResourceIdLedger(
        os.path.join(root, ".katana", "tombstones.json"),
        prefix="wf-",
    )
    manifest = TransactionManifest(os.path.join(root, ".katana", "manifests"))
    kernel.bind("work-folder", _wf_policy(), vfs, ledger, manifest, root)
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


def _do_search(query: str, top_k: int) -> list[dict]:
    """把 vault-search locator 收敛为 folder_id + relative filename。"""
    response = vault_search.search(query, top_k=top_k)
    results: list[dict] = []
    for hit in response.results:
        locator = str(hit.path).replace("\\", "/").lstrip("./")
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
    return results


@mcp.tool()
async def wf_search(query: str, top_k: int = 10) -> list[dict]:
    """搜索 Work Folder，返回 ``folder_id`` 与 folder-relative filename。"""
    return _do_search(query, top_k)


@mcp.tool()
async def wf_create(
    topic: str,
    expected_base_sha: str | None = None,
) -> dict:
    """在 data root 直接创建新的 ``wf-ID/`` folder。"""
    return _server_mutation(
        lambda: _require_store().create(
            topic,
            now_fn=_now,
            expected_base_sha=expected_base_sha,
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
        )
    )


@mcp.tool()
async def wf_resume(
    folder_id: str,
    expected_base_sha: str | None = None,
) -> dict:
    """用 opaque folder ID 恢复并验证工作状态。"""
    return _server_mutation(
        lambda: _require_store().resume(
            folder_id,
            now_fn=_now,
            expected_base_sha=expected_base_sha,
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
) -> dict:
    """重建无 locator 列的顶层 INDEX.md。"""
    return _server_mutation(
        lambda: _require_store().reindex(
            dry_run=dry_run,
            expected_base_sha=expected_base_sha,
        )
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
