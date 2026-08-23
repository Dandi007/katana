"""credstore: persistence roundtrip, fail-closed load, atomicity, perms."""

import json
import os
import time

import pytest

from katana_remote.auth import CredentialEntry, CredentialRegistry, hash_token
from katana_remote.credstore import (
    FILE_VERSION,
    TOKEN_PREFIX,
    generate_token,
    load_entries,
    load_registry,
    save_entries,
)


def _entry(token, tenant="alice", **kw):
    defaults = dict(
        principal_id=tenant,
        tenant=tenant,
        domains={"memory", "wiki"},
        scopes={"read", "query"},
    )
    defaults.update(kw)
    return CredentialEntry(token_hash=hash_token(token), **defaults)


def test_generate_token_shape():
    t1, t2 = generate_token(), generate_token()
    assert t1.startswith(TOKEN_PREFIX) and t2.startswith(TOKEN_PREFIX)
    assert t1 != t2
    assert len(t1) > 40


def test_save_load_roundtrip_authenticates(tmp_path):
    path = tmp_path / "credentials.json"
    token = generate_token()
    save_entries(path, [_entry(token)])

    registry = load_registry(path)
    auth = registry.authenticate(token)
    assert auth is not None
    assert auth.principal.tenant == "alice"
    assert auth.principal.scopes == {"read", "query"}
    assert registry.authenticate("ktn_wrong") is None


def test_plaintext_never_in_file(tmp_path):
    path = tmp_path / "credentials.json"
    token = generate_token()
    save_entries(path, [_entry(token)])
    raw = path.read_text()
    assert token not in raw
    assert hash_token(token) in raw


def test_missing_file_is_empty_registry(tmp_path):
    registry = load_registry(tmp_path / "absent.json")
    assert len(registry) == 0


def test_file_mode_0600(tmp_path):
    path = tmp_path / "credentials.json"
    save_entries(path, [_entry(generate_token())])
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_overwrite_replaces_content(tmp_path):
    path = tmp_path / "credentials.json"
    t1, t2 = generate_token(), generate_token()
    save_entries(path, [_entry(t1)])
    save_entries(path, [_entry(t2, tenant="bob")])
    registry = load_registry(path)
    assert registry.authenticate(t1) is None
    assert registry.authenticate(t2) is not None
    assert len(load_entries(path)) == 1


def test_version_mismatch_raises(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"version": 99, "credentials": []}))
    with pytest.raises(ValueError, match="version"):
        load_entries(path)


def test_unknown_field_raises(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({
        "version": FILE_VERSION,
        "credentials": [{"token_hash": "sha256:x", "principal_id": "a",
                         "tenant": "a", "plaintext": "leak"}],
    }))
    with pytest.raises(ValueError, match="unknown"):
        load_entries(path)


def test_revoked_and_expired_survive_roundtrip(tmp_path):
    path = tmp_path / "credentials.json"
    t_revoked, t_expired, t_ok = generate_token(), generate_token(), generate_token()
    e_revoked = _entry(t_revoked, revoked=True)
    e_expired = _entry(t_expired, expires_at=time.time() - 10)
    save_entries(path, [e_revoked, e_expired, _entry(t_ok)])

    registry = load_registry(path)
    assert registry.authenticate(t_revoked) is None
    assert registry.authenticate(t_expired) is None
    assert registry.authenticate(t_ok) is not None


def test_add_entry_registry():
    registry = CredentialRegistry()
    token = generate_token()
    registry.add_entry(_entry(token))
    assert registry.authenticate(token) is not None
