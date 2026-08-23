"""Env-driven remote wiring: file-loaded registry + FileAuditLogger end to end.

Mimics what a server main() does in remote mode: mint via credctl into a
credstore file, load_registry() from it, wrap the memory app, and verify
401 / cross-tenant 403 / authenticated call + persisted audit trail.
"""

import json
import subprocess

from starlette.testclient import TestClient

from katana_remote import FileAuditLogger, load_registry
from katana_remote.credctl import main as credctl
from katana_remote.middleware import create_remote_app
from katana_remote.runtime import audit_logger_from_env, remote_credentials_path
from katana_memory_mcp import server as memory_server

_MCP_ACCEPT = {"Accept": "application/json, text/event-stream"}


def _mint(capsys, path, tenant, scopes="read,query,mutate,command"):
    rc = credctl(["--file", str(path), "mint", "--principal", tenant,
                  "--tenant", tenant, "--domains", "memory",
                  "--scopes", scopes])
    assert rc == 0
    return capsys.readouterr().out.strip().splitlines()[-1]


def _build_app(tmp_path, registry, audit_logger):
    subprocess.run(["git", "init"], cwd=tmp_path / "data", check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path / "data", check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path / "data", check=True)
    (tmp_path / "data" / "uther").mkdir()
    inner = memory_server.build_app(str(tmp_path / "data"))
    return create_remote_app(inner, credential_registry=registry,
                             audit_logger=audit_logger, domain="memory")


def _initialize(client, token, tenant):
    return client.post(
        f"/t/{tenant}/mcp",
        json={"jsonrpc": "2.0", "method": "initialize",
              "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "1"}},
              "id": 1},
        headers={"Authorization": f"Bearer {token}", **_MCP_ACCEPT},
    )


def test_file_backed_auth_end_to_end(tmp_path, capsys):
    creds_file = tmp_path / "credentials.json"
    (tmp_path / "data").mkdir()
    token_uther = _mint(capsys, creds_file, "uther")
    token_other = _mint(capsys, creds_file, "guest-a")
    audit_path = tmp_path / "audit.jsonl"
    app = _build_app(tmp_path, load_registry(creds_file), FileAuditLogger(audit_path))

    with TestClient(app) as client:
        # no token → 401
        r = client.post("/t/uther/mcp", json={"method": "initialize"}, headers=_MCP_ACCEPT)
        assert r.status_code == 401
        # cross-tenant token → 403
        r = _initialize(client, token_other, "uther")
        assert r.status_code == 403
        # own tenant → session established
        r = _initialize(client, token_uther, "uther")
        assert r.status_code == 200
        assert r.headers.get("mcp-session-id")

    # audit trail persisted as JSONL (authenticated requests), no plaintext token
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) >= 1
    records = [json.loads(l) for l in lines]
    assert any(rec["result"] == "success" and rec["tenant"] == "uther" for rec in records)
    assert all(token_uther not in l and token_other not in l for l in lines)


def test_runtime_env_helpers(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("KATANA_REMOTE_CREDENTIALS", raising=False)
    monkeypatch.delenv("KATANA_REMOTE_AUDIT_LOG", raising=False)
    assert remote_credentials_path() is None
    assert type(audit_logger_from_env()).__name__ == "AuditLogger"

    creds_file = tmp_path / "credentials.json"
    token = _mint(capsys, creds_file, "uther")
    monkeypatch.setenv("KATANA_REMOTE_CREDENTIALS", str(creds_file))
    monkeypatch.setenv("KATANA_REMOTE_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    assert remote_credentials_path() == str(creds_file)
    assert isinstance(audit_logger_from_env(), FileAuditLogger)

    from katana_remote.runtime import registry_from_env
    assert registry_from_env().authenticate(token) is not None
