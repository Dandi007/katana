"""credctl CLI: mint prints plaintext once, revoke kills auth, list redacts."""

import time

from katana_remote.credctl import main as credctl
from katana_remote.credstore import load_entries, load_registry


def _mint(capsys, path, tenant="alice", scopes="read,query,mutate,command",
          extra=None):
    argv = ["--file", str(path), "mint",
            "--principal", tenant, "--tenant", tenant,
            "--domains", "memory,wiki,work-folder", "--scopes", scopes]
    if extra:
        argv += extra
    rc = credctl(argv)
    assert rc == 0
    token = capsys.readouterr().out.strip().splitlines()[-1]
    assert token.startswith("ktn_")
    return token


def test_mint_then_authenticate(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    token = _mint(capsys, path)
    auth = load_registry(path).authenticate(token)
    assert auth is not None
    assert auth.principal.tenant == "alice"
    assert auth.principal.domains == {"memory", "wiki", "work-folder"}
    assert token not in path.read_text()


def test_mint_rejects_unknown_scope(tmp_path, capsys):
    rc = credctl(["--file", str(tmp_path / "c.json"), "mint",
                  "--principal", "a", "--tenant", "a",
                  "--domains", "memory", "--scopes", "read,superuser"])
    assert rc == 2
    assert not (tmp_path / "c.json").exists()


def test_mint_expires_days(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    _mint(capsys, path, extra=["--expires-days", "30"])
    (entry,) = load_entries(path)
    assert entry.expires_at is not None
    assert abs(entry.expires_at - (time.time() + 30 * 86400)) < 60


def test_revoke_by_token(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    token = _mint(capsys, path)
    rc = credctl(["--file", str(path), "revoke", "--token", token])
    assert rc == 0
    assert load_registry(path).authenticate(token) is None


def test_revoke_by_hash(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    token = _mint(capsys, path)
    (entry,) = load_entries(path)
    rc = credctl(["--file", str(path), "revoke", "--hash", entry.token_hash])
    assert rc == 0
    assert load_registry(path).authenticate(token) is None


def test_revoke_unknown_fails(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    _mint(capsys, path)
    assert credctl(["--file", str(path), "revoke", "--token", "ktn_nope"]) == 1


def test_list_redacts_and_shows_status(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    token = _mint(capsys, path)
    credctl(["--file", str(path), "revoke", "--token", token])
    capsys.readouterr()
    assert credctl(["--file", str(path), "list"]) == 0
    out = capsys.readouterr().out
    assert token not in out
    assert "revoked" in out
    assert "tenant=alice" in out


def test_multiple_tenants_coexist(tmp_path, capsys):
    path = tmp_path / "credentials.json"
    t_a = _mint(capsys, path, tenant="alice")
    t_b = _mint(capsys, path, tenant="bob", scopes="read,query")
    registry = load_registry(path)
    assert registry.authenticate(t_a).principal.tenant == "alice"
    auth_b = registry.authenticate(t_b)
    assert auth_b.principal.tenant == "bob"
    assert "mutate" not in auth_b.principal.scopes
