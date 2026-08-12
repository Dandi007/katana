import asyncio
import pathlib
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
    # path must not be exposed to callers
    assert "path" not in got


def test_update_and_delete_commit(srv):
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create", {"name": "u-card", "description": "d", "body": "## Fact\ntest\n\n## How to Verify\ntest"})["id"]
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


# ── /index.md 纯文本 route（kimi-code / OpenCode 等非 Claude runtime 消费） ────

def test_index_md_route_returns_plain_text(tmp_path):
    app = server.build_app(_data_root(tmp_path))
    with TestClient(app) as tc:
        r = tc.get("/t/uther/index.md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")
        assert r.text.startswith("<memory-index>")
        assert "seed-card" in r.text
        # 纯文本，不是 Claude hook JSON 包裹
        assert "hookSpecificOutput" not in r.text


def test_index_md_route_unknown_tenant_404(tmp_path):
    app = server.build_app(_data_root(tmp_path))
    with TestClient(app) as tc:
        assert tc.get("/t/nobody/index.md").status_code == 404


def test_mcp_mounted_per_tenant(tmp_path):
    app = server.build_app(_data_root(tmp_path))
    with TestClient(app) as tc:
        # streamable-http endpoint 存在（非 404）；MCP 握手细节不在此测
        assert tc.post("/t/uther/mcp", json={}).status_code != 404


# ── memory_read / memory_edit 工具（FS-Read/Edit 语义） ──────────────────────

def test_memory_read_then_edit_then_get(srv):
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create",
                {"name": "r-card", "description": "d",
                 "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    rd = _call(mcp, "memory_read", {"id": cid})
    assert rd["content"].splitlines()[0] == "1\t---"
    assert "## Fact" in rd["content"]
    assert "path" not in rd
    ed = _call(mcp, "memory_edit",
               {"id": cid, "old_string": "## Fact\nx", "new_string": "## Fact\nz"})
    assert ed["git"]["committed"] is True
    assert "## Fact\nz" in _call(mcp, "memory_get", {"id": cid})["body"]


def test_memory_read_offset_limit(srv):
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create",
                {"name": "p-card", "description": "d",
                 "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    rd = _call(mcp, "memory_read", {"id": cid, "offset": 1, "limit": 1})
    assert rd["content"].splitlines() == ["1\t---"]


def test_memory_edit_error_is_tool_error(srv):
    mcp, _, _ = srv
    cid = _call(mcp, "memory_create",
                {"name": "e-card", "description": "d",
                 "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    with pytest.raises(Exception):
        _call(mcp, "memory_edit", {"id": cid, "old_string": "absent", "new_string": "z"})


# ── v2: memory_get 命中记账 + pinned 传参 ──────────────────────────────────────

def test_memory_get_appends_access_log(srv, tmp_path_factory, monkeypatch):
    state = tmp_path_factory.mktemp("state")   # 必须在治理仓之外
    monkeypatch.setenv("KATANA_MEMORY_STATE_DIR", str(state))
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create", {"name": "log-card", "description": "d",
                                       "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    _call(mcp, "memory_get", {"id": cid})
    _call(mcp, "memory_get", {"id": cid})
    import json as _json
    log = state / "memory-access-log.jsonl"
    recs = [_json.loads(l) for l in log.read_text().splitlines()]
    assert sum(1 for r in recs if r["id"] == cid and r["tenant"] == "uther") == 2


def test_access_log_stays_out_of_governed_repo(srv, tmp_path_factory, monkeypatch):
    """读路径的 telemetry 不得落进数据仓——否则它会弄脏仓库并阻塞写路径。"""
    monkeypatch.setenv("KATANA_MEMORY_STATE_DIR", str(tmp_path_factory.mktemp("state")))
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create", {"name": "iso-card", "description": "d",
                                       "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    _call(mcp, "memory_get", {"id": cid})
    assert not (pathlib.Path(repo) / ".katana" / "memory-access-log.jsonl").exists()


def test_read_then_write_is_not_self_locked(srv, tmp_path_factory, monkeypatch):
    """回归：memory_get 之后必须还能 memory_create。

    历史 bug：_log_access 把日志写进治理仓，读操作使仓库变脏，
    随后的写操作被 "repository has tracked, staged, or untracked changes" 拒绝。
    """
    monkeypatch.setenv("KATANA_MEMORY_STATE_DIR", str(tmp_path_factory.mktemp("state")))
    mcp, tdir, repo = srv
    first = _call(mcp, "memory_create", {"name": "lock-a", "description": "d",
                                         "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    _call(mcp, "memory_get", {"id": first})
    second = _call(mcp, "memory_create", {"name": "lock-b", "description": "d",
                                          "body": "## Fact\nx\n\n## How to Verify\ny"})
    assert second["git"]["committed"] is True


def test_memory_update_pinned_roundtrip(srv):
    mcp, tdir, repo = srv
    cid = _call(mcp, "memory_create", {"name": "pin-card", "description": "d",
                                       "body": "## Fact\nx\n\n## How to Verify\ny"})["id"]
    upd = _call(mcp, "memory_update", {"id": cid, "pinned": True})
    assert upd["pinned"] is True
    idx = _call(mcp, "memory_index")
    assert [c["pinned"] for c in idx["cards"] if c["id"] == cid] == [True]
    upd2 = _call(mcp, "memory_update", {"id": cid, "pinned": False})
    assert upd2["pinned"] is False
