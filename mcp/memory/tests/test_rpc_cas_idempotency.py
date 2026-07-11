"""Real-app CAS + idempotency contract through FastMCP tools (operator P1 #7).

Drives the actual Memory fs_* tools over a FastMCP Client (not the shared unit
API) to prove: same-id/same-payload replay returns the original commit with no
second effect; same-id/different-payload trips IDEMPOTENCY_CONFLICT; a stale
expected_base_commit trips a CAS conflict; and after deleting the operational
receipt mirror, a replay still returns the original commit (rebuilt from Git).
"""
import asyncio
import subprocess

import pytest
from fastmcp import Client

from katana_memory_mcp import server

_CARD = ("---\nid: m-abc123\nname: rpc-card\ndescription: d\nstatus: active\n"
         "---\n\n## Fact\nx\n\n## How to Verify\ny\n")


def _init(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "uther").mkdir()
    return str(tmp_path)


def _call(mcp, tool, args):
    async def go():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args)).data
    return asyncio.run(go())


@pytest.fixture
def srv(tmp_path):
    repo = _init(tmp_path)
    return server.build_tenant_server("uther", str(tmp_path / "uther"), repo), repo


def _head(repo):
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_same_id_same_payload_replays_to_same_commit(srv):
    mcp, repo = srv
    r1 = _call(mcp, "fs_create", {"virtual_path": "rpc-card.md", "content": _CARD,
                                  "mutation_id": "mut-1"})
    head1 = _head(repo)
    r2 = _call(mcp, "fs_create", {"virtual_path": "rpc-card.md", "content": _CARD,
                                  "mutation_id": "mut-1"})
    assert r2["commit_sha"] == r1["commit_sha"]
    assert _head(repo) == head1  # no second commit


def test_same_id_different_payload_conflicts(srv):
    mcp, repo = srv
    _call(mcp, "fs_create", {"virtual_path": "rpc-card.md", "content": _CARD,
                             "mutation_id": "mut-1"})
    other = _CARD.replace("## Fact\nx", "## Fact\nDIFFERENT")
    with pytest.raises(Exception) as ei:
        _call(mcp, "fs_create", {"virtual_path": "rpc-card.md", "content": other,
                                 "mutation_id": "mut-1"})
    assert "IDEMPOTENCY_CONFLICT" in str(ei.value)


def test_stale_base_commit_conflicts(srv):
    mcp, repo = srv
    _call(mcp, "fs_create", {"virtual_path": "rpc-card.md", "content": _CARD})
    stale = "deadbeef" * 5
    with pytest.raises(Exception) as ei:
        _call(mcp, "fs_write", {"virtual_path": "rpc-card.md",
                                "content": _CARD.replace("x\n", "z\n"),
                                "expected_base_commit": stale})
    assert "BASE_COMMIT_CONFLICT" in str(ei.value)


def test_replay_after_receipt_mirror_deleted(srv, tmp_path):
    mcp, repo = srv
    r1 = _call(mcp, "fs_create", {"virtual_path": "rpc-card.md", "content": _CARD,
                                  "mutation_id": "mut-1"})
    # Simulate operational-mirror loss.
    receipts = tmp_path / ".kb" / "receipts.json"
    if receipts.exists():
        receipts.unlink()
    # A fresh tenant server rebuilds receipts from Git history on reconcile.
    mcp2 = server.build_tenant_server("uther", str(tmp_path / "uther"), str(tmp_path))
    from katana_kb_mcp_shared.kernel import TransactionEngine
    TransactionEngine(str(tmp_path), domain="memory").reconcile()
    r2 = _call(mcp2, "fs_create", {"virtual_path": "rpc-card.md", "content": _CARD,
                                   "mutation_id": "mut-1"})
    assert r2["commit_sha"] == r1["commit_sha"]
