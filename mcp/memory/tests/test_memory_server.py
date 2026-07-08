import asyncio
import subprocess

import pytest
from fastmcp import Client
from starlette.testclient import TestClient

from katana_memory_mcp import server, store


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    d = tmp_path / "uther"
    d.mkdir()
    return str(tmp_path), str(d)


def _data_root(tmp_path):
    repo, tdir = _init_repo(tmp_path)
    store.create_card(tdir, "seed-card", "seed desc", "## Fact\nx\n\n## How to Verify\ny", now="2026-07-08")
    return repo


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


# ── Task 6: build_app 多租户组装 + /index hook route ──────────────────────────

def test_index_route_returns_hook_json(tmp_path):
    app = server.build_app(_data_root(tmp_path))
    with TestClient(app) as tc:
        r = tc.get("/t/uther/index")
        assert r.status_code == 200
        payload = r.json()
        ac = payload["hookSpecificOutput"]["additionalContext"]
        assert "<memory-index>" in ac and "seed-card" in ac


def test_index_route_unknown_tenant_404(tmp_path):
    app = server.build_app(_data_root(tmp_path))
    with TestClient(app) as tc:
        assert tc.get("/t/nobody/index").status_code == 404


def test_mcp_mounted_per_tenant(tmp_path):
    app = server.build_app(_data_root(tmp_path))
    with TestClient(app) as tc:
        # streamable-http endpoint 存在（非 404）；MCP 握手细节不在此测
        assert tc.post("/t/uther/mcp", json={}).status_code != 404
