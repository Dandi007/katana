"""Smoke test: work-folder app with remote auth layer via HTTP.

Verifies that build_remote_app wraps the work-folder app correctly,
that /livez is reachable without auth, and that an authenticated
tool call works over HTTP.
"""

import json
import subprocess

import pytest

from katana_remote import (
    CredentialRegistry,
    AuditLogger,
    UNAUTHORIZED,
    FORBIDDEN,
)
from katana_work_folder_mcp import server as wf_server
from katana_work_folder_mcp.reindex import render_index
from starlette.testclient import TestClient


def _init_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(
        "/.katana/runtime/\n",
        encoding="utf-8",
    )
    (tmp_path / "INDEX.md").write_text(render_index([]), encoding="utf-8")
    controls = tmp_path / ".katana"
    controls.mkdir()
    (controls / "flat-layout.json").write_text(
        '{"layout":"flat-id-v1","schema_version":1}\n',
        encoding="utf-8",
    )
    (controls / "tombstones.json").write_text(
        '{"tombstones":[]}\n',
        encoding="utf-8",
    )
    (controls / "legacy-manifest-inventory.json").write_text(
        '{"manifests":[],"schema_version":1}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "flat fixture"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return str(tmp_path)


def _make_wf_app_with_auth(tmp_path, credential_registry, **kwargs):
    wf_root = _init_repo(tmp_path)
    return wf_server.build_remote_app(
        repo_root=wf_root,
        credential_registry=credential_registry,
        **kwargs,
    )


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}


def _mcp_session(client, token, tenant="default"):
    headers = {**_auth_header(token), **_MCP_ACCEPT}
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
            "id": 1,
        },
        headers=headers,
    )
    assert r.status_code == 200, f"Session init failed: {r.status_code} {r.text[:200]}"
    session_id = r.headers.get("mcp-session-id")
    assert session_id, f"No session ID: {dict(r.headers)}"
    return session_id


def _mcp_call(client, token, session_id, tool_name, arguments=None):
    headers = {
        **_auth_header(token),
        **_MCP_ACCEPT,
        "mcp-session-id": session_id,
    }
    params = {"name": tool_name}
    if arguments is not None:
        params["arguments"] = arguments
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "tools/call", "params": params, "id": 2},
        headers=headers,
    )
    return r


def _tool_result(response):
    for line in response.text.splitlines():
        if not line.startswith("data: "):
            continue
        envelope = json.loads(line[6:])
        result = envelope.get("result", {})
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                return json.loads(item["text"])
    envelope = response.json()
    result = envelope.get("result", {})
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            return json.loads(item["text"])
    raise AssertionError(f"tool response has no JSON text content: {response.text}")


class TestWorkFolderRemote:
    def test_livez_no_auth(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("token", "alice", "default", scopes={"read", "mutate", "query", "operate", "audit"})
        app = _make_wf_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            r = client.get("/livez")
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

    def test_unauthorized_no_token(self, tmp_path):
        creds = CredentialRegistry()
        app = _make_wf_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            r = client.post("/mcp", json={"method": "tools/call", "params": {"name": "fs_capabilities"}})
            assert r.status_code == UNAUTHORIZED

    def test_authenticated_fs_capabilities_works(self, tmp_path):
        creds = CredentialRegistry()
        creds.register("full-token", "alice", "default", scopes={"read", "mutate", "query", "operate", "audit"})
        app = _make_wf_app_with_auth(tmp_path, creds)
        with TestClient(app) as client:
            session_id = _mcp_session(client, "full-token")
            r = _mcp_call(client, "full-token", session_id, "fs_capabilities")
            assert r.status_code == 200, f"fs_capabilities failed: {r.status_code} {r.text[:200]}"

    def test_idempotency_keys_are_namespaced_per_authenticated_principal(
        self,
        tmp_path,
    ):
        creds = CredentialRegistry()
        scopes = {"read", "mutate", "query", "operate", "audit"}
        creds.register("alice-token", "alice", "default", scopes=scopes)
        creds.register("bob-token", "bob", "default", scopes=scopes)
        app = _make_wf_app_with_auth(tmp_path, creds)

        with TestClient(app) as client:
            alice_session = _mcp_session(client, "alice-token")
            bob_session = _mcp_session(client, "bob-token")
            alice = _tool_result(
                _mcp_call(
                    client,
                    "alice-token",
                    alice_session,
                    "wf_create",
                    {
                        "topic": "alice-topic",
                        "idempotency_key": "shared-client-key",
                    },
                )
            )
            bob = _tool_result(
                _mcp_call(
                    client,
                    "bob-token",
                    bob_session,
                    "wf_create",
                    {
                        "topic": "bob-topic",
                        "idempotency_key": "shared-client-key",
                    },
                )
            )

        assert alice["created"] is True
        assert bob["created"] is True
        assert alice["folder_id"] != bob["folder_id"]
        assert alice["mutation_id"] != bob["mutation_id"]
