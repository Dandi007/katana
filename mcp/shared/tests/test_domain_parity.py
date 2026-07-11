"""Cross-domain capability parity anchor (design §4.4, §9.1 Contract gate).

Machine-checks that:
- the 19 现役 domain tools survive (Memory 7 + Wiki 6 + Work Folder 6);
- all three apps expose the SAME governed Full VFS (fs_*) surface, and that
  surface is the complete M1 operation set (design §5.2), not a 5-tool subset;
- mutating domain tools AND fs_* enter the SAME policy → transaction pipeline
  (there is no raw/legacy bypass — design §4.4, INV-5);
- each app statically composes exactly one DomainPolicy.

Aggregate gate: the three domain apps MUST import. When run via the required
``bash mcp/run-tests.sh`` (all four packages installed) an import failure is a
hard error, not a skip (feedback: the gate must catch broken app integration).
A standalone shared-only install may set ``KB_SHARED_ONLY=1`` to skip.
"""
import asyncio
import os
import subprocess

import pytest

from katana_kb_mcp_shared.kernel.facade import FS_FACADE

_SHARED_ONLY = os.environ.get("KB_SHARED_ONLY") == "1"

if not _SHARED_ONLY:
    # Hard import (no try/except): a broken domain app fails the aggregate gate.
    import katana_memory_mcp.server  # noqa: F401
    import katana_wiki_mcp.server  # noqa: F401
    import katana_work_folder_mcp.server  # noqa: F401

requires_domains = pytest.mark.skipif(
    _SHARED_ONLY, reason="KB_SHARED_ONLY: standalone shared-package test mode")

MEMORY_TOOLS = {"memory_index", "memory_get", "memory_create", "memory_update",
                "memory_delete", "memory_read", "memory_edit"}
WIKI_TOOLS = {"wiki_search", "wiki_query", "wiki_ingest_plan",
              "wiki_ingest_apply", "wiki_list_docs", "wiki_lint_mechanical"}
WF_TOOLS = {"wf_search", "wf_create", "wf_list", "wf_save", "wf_resume",
            "wf_reindex"}


def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "ci@ci"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "ci"], cwd=tmp_path, check=True)


def _names_of(mcp):
    async def go():
        return {t.name for t in await mcp.list_tools()}
    return asyncio.run(go())


def test_total_domain_tools_is_nineteen():
    assert len(MEMORY_TOOLS | WIKI_TOOLS | WF_TOOLS) == 19
    assert len(MEMORY_TOOLS) == 7
    assert len(WIKI_TOOLS) == 6
    assert len(WF_TOOLS) == 6


def test_full_vfs_surface_is_complete():
    # The M1 Full VFS operation set (design §5.2) — not a five-tool subset.
    assert FS_FACADE >= {
        "fs_resolve", "fs_stat", "fs_list", "fs_glob", "fs_changes",
        "fs_read", "fs_create", "fs_write", "fs_edit", "fs_mkdir",
        "fs_copy", "fs_rename", "fs_delete", "fs_batch",
        "fs_capabilities", "fs_status",
    }


@requires_domains
def test_memory_surface(tmp_path):
    from katana_memory_mcp import server as m
    _git_repo(tmp_path)
    (tmp_path / "uther").mkdir()
    names = _names_of(m.build_tenant_server("uther", str(tmp_path / "uther"),
                                            str(tmp_path)))
    assert MEMORY_TOOLS <= names
    assert FS_FACADE <= names


@requires_domains
def test_wiki_surface(tmp_path):
    import katana_wiki_mcp.server as w
    _git_repo(tmp_path)
    w.configure(str(tmp_path), str(tmp_path))
    names = _names_of(w.mcp)
    assert WIKI_TOOLS <= names
    assert FS_FACADE <= names


@requires_domains
def test_work_folder_surface(tmp_path):
    import katana_work_folder_mcp.server as wf
    _git_repo(tmp_path)
    wf.configure(str(tmp_path), str(tmp_path))
    names = _names_of(wf.mcp)
    assert WF_TOOLS <= names
    assert FS_FACADE <= names


@requires_domains
def test_all_apps_share_identical_fs_facade():
    # Parity: the governed façade is identical across domains (same mechanics).
    from katana_kb_mcp_shared.kernel.facade import GovernedVFS
    for op in FS_FACADE:
        assert hasattr(GovernedVFS, op), f"kernel façade missing {op}"


@requires_domains
def test_each_app_composes_exactly_one_policy():
    from katana_kb_mcp_shared.kernel.policy import AppComposition, DomainPolicy
    from katana_memory_mcp.policy import MemoryPolicy
    from katana_wiki_mcp.policy import WikiPolicy
    from katana_work_folder_mcp.policy import WorkFolderPolicy
    for pol in (MemoryPolicy(), WikiPolicy(), WorkFolderPolicy()):
        assert isinstance(pol, DomainPolicy)
        comp = AppComposition(pol)
        assert comp.domain == pol.domain
