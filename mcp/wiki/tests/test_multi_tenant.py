"""wiki 单实例多租户：per-tenant mount、数据隔离、legacy default mount、remote 403。"""

import json
import subprocess

import pytest
from starlette.testclient import TestClient

from katana_wiki_mcp import server as srv

_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}

_VALID_PAGE = """---
创建日期: 2026-08-23
tags:
  - test
类型: 卡片
摘要: 多租户隔离测试页
sources:
  - unit-test
---

正文内容，引用 [[占位页]]。
"""


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=path, check=True, capture_output=True)
    return str(path)


def _two_tenant_app(tmp_path, default_tenant=None):
    root_a = _git_repo(tmp_path / "wiki-a")
    root_b = _git_repo(tmp_path / "wiki-b")
    tenant_map = {"alice": root_a, "bob": root_b}
    return srv.build_app(tenant_map, str(tmp_path), default_tenant), tenant_map


def _initialize(client, base):
    r = client.post(
        f"{base}/mcp",
        json={"jsonrpc": "2.0", "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "1"}},
              "id": 1},
        headers=_MCP_ACCEPT,
    )
    assert r.status_code == 200, r.text[:200]
    return r.headers["mcp-session-id"]


def _call(client, base, session_id, tool, arguments=None):
    r = client.post(
        f"{base}/mcp",
        json={"jsonrpc": "2.0", "method": "tools/call",
              "params": {"name": tool, "arguments": arguments or {}}, "id": 2},
        headers={**_MCP_ACCEPT, "mcp-session-id": session_id},
    )
    assert r.status_code == 200, r.text[:200]
    for line in r.text.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    return json.loads(r.text)


def _tool_payload(rpc):
    content = rpc["result"]["content"][0]["text"]
    return json.loads(content)


def test_tenants_mounted_and_isolated(tmp_path):
    app, _ = _two_tenant_app(tmp_path)
    with TestClient(app) as client:
        sid_a = _initialize(client, "/t/alice")
        sid_b = _initialize(client, "/t/bob")

        created = _tool_payload(_call(client, "/t/alice", sid_a, "fs_create",
                                      {"path": "note.md", "content": _VALID_PAGE}))
        assert created.get("code") is None, created

        docs_a = _call(client, "/t/alice", sid_a, "wiki_list_docs")
        docs_b = _call(client, "/t/bob", sid_b, "wiki_list_docs")
        paths_a = [d["path"] for d in json.loads(docs_a["result"]["content"][0]["text"])] \
            if docs_a["result"]["content"] else []
        text_b = docs_b["result"]["content"][0]["text"] if docs_b["result"]["content"] else "[]"
        assert any("note.md" in p for p in paths_a)
        assert "note.md" not in text_b

        read_b = _call(client, "/t/bob", sid_b, "fs_read", {"path": "note.md"})
        payload_b = _tool_payload(read_b) if read_b["result"]["content"] else {}
        assert read_b["result"].get("isError") or payload_b.get("code") is not None


def test_unknown_tenant_404(tmp_path):
    app, _ = _two_tenant_app(tmp_path)
    with TestClient(app) as client:
        r = client.post("/t/mallory/mcp", json={"method": "initialize"}, headers=_MCP_ACCEPT)
        assert r.status_code == 404


def test_legacy_default_mount(tmp_path):
    app, tenant_map = _two_tenant_app(tmp_path, default_tenant="alice")
    with TestClient(app) as client:
        sid = _initialize(client, "")
        created = _tool_payload(_call(client, "", sid, "fs_create",
                                      {"path": "legacy.md", "content": _VALID_PAGE}))
        assert created.get("code") is None, created
    import os
    assert os.path.isfile(os.path.join(tenant_map["alice"], "legacy.md"))
    assert not os.path.exists(os.path.join(tenant_map["bob"], "legacy.md"))


def test_default_tenant_must_be_in_map(tmp_path):
    root = _git_repo(tmp_path / "wiki-a")
    with pytest.raises(ValueError, match="default tenant"):
        srv.build_app({"alice": root}, str(tmp_path), "ghost")


def test_load_tenant_map_validation(tmp_path):
    good = tmp_path / "tenants.json"
    good.write_text(json.dumps({"alice": "/data/x", "bob-2": "/data/y"}))
    assert srv.load_tenant_map(good) == {"alice": "/data/x", "bob-2": "/data/y"}

    bad_name = tmp_path / "bad-name.json"
    bad_name.write_text(json.dumps({"../evil": "/data/x"}))
    with pytest.raises(ValueError, match="invalid tenant name"):
        srv.load_tenant_map(bad_name)

    bad_root = tmp_path / "bad-root.json"
    bad_root.write_text(json.dumps({"alice": ""}))
    with pytest.raises(ValueError, match="root"):
        srv.load_tenant_map(bad_root)

    empty = tmp_path / "empty.json"
    empty.write_text("{}")
    with pytest.raises(ValueError, match="non-empty"):
        srv.load_tenant_map(empty)


def test_remote_wrap_cross_tenant_403(tmp_path):
    from katana_remote import CredentialRegistry

    root_a = _git_repo(tmp_path / "wiki-a")
    root_b = _git_repo(tmp_path / "wiki-b")
    registry = CredentialRegistry()
    registry.register("token-alice", "alice", "alice",
                      scopes={"read", "query", "mutate", "command"})
    app = srv.build_remote_app(
        root_a, str(tmp_path), registry,
        tenant_map={"alice": root_a, "bob": root_b},
    )
    with TestClient(app) as client:
        # 无 token → 401
        r = client.post("/t/alice/mcp", json={"method": "initialize"}, headers=_MCP_ACCEPT)
        assert r.status_code == 401
        # alice 的 token 访问 bob → 403
        r = client.post(
            "/t/bob/mcp",
            json={"jsonrpc": "2.0", "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}},
                  "id": 1},
            headers={"Authorization": "Bearer token-alice", **_MCP_ACCEPT},
        )
        assert r.status_code == 403
        # alice 的 token 访问 alice → 通
        r = client.post(
            "/t/alice/mcp",
            json={"jsonrpc": "2.0", "method": "initialize",
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                             "clientInfo": {"name": "t", "version": "1"}},
                  "id": 1},
            headers={"Authorization": "Bearer token-alice", **_MCP_ACCEPT},
        )
        assert r.status_code == 200
