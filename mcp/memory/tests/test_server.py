import asyncio
import subprocess

import pytest
from fastmcp import Client

from katana_memory_mcp import server, store


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    d = tmp_path / "uther"
    d.mkdir()
    return str(tmp_path), str(d)


def _call(mcp, tool, args=None):
    async def go():
        async with Client(mcp) as c:
            return (await c.call_tool(tool, args or {})).data
    return asyncio.run(go())


@pytest.fixture
def srv(tmp_path):
    repo, tdir = _init_repo(tmp_path)
    return server.build_tenant_server("uther", tdir, repo), tdir, repo


def test_create_then_index_then_get(srv):
    mcp, tdir, repo = srv
    created = _call(mcp, "memory_create", {
        "name": "t-card", "description": "d", "body": "## Fact\nx\n\n## How to Verify\ny"})
    assert store.ID_RE.fullmatch(created["id"])
    assert created["git"]["committed"] is True
    idx = _call(mcp, "memory_index")
    assert idx["cards"][0]["id"] == created["id"]
    got = _call(mcp, "memory_get", {"id": created["id"]})
    assert "## How to Verify" in got["body"]


def test_update_and_delete_commit(srv):
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create", {"name": "u-card", "description": "d", "body": "b"})["id"]
    upd = _call(mcp, "memory_update", {"id": cid, "status": "stale"})
    assert upd["status"] == "stale" and upd["git"]["committed"] is True
    del_ = _call(mcp, "memory_delete", {"id": cid})
    assert del_["git"]["committed"] is True
    assert _call(mcp, "memory_index")["cards"] == []


def test_get_unknown_id_is_tool_error(srv):
    mcp, _, _ = srv
    with pytest.raises(Exception):
        _call(mcp, "memory_get", {"id": "m-ffffff"})
