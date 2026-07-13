"""Tests for auth module: credential registry, bearer token, hash storage."""
import time

from katana_remote.auth import (
    CredentialRegistry,
    extract_bearer_token,
    hash_token,
    UNAUTHORIZED,
    FORBIDDEN,
)


def test_hash_token_is_deterministic():
    h1 = hash_token("test-token")
    h2 = hash_token("test-token")
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_hash_token_does_not_contain_plaintext():
    h = hash_token("my-secret-token")
    assert "my-secret-token" not in h
    assert "secret" not in h


def test_extract_bearer_token_valid():
    assert extract_bearer_token({"authorization": "Bearer abc123"}) == "abc123"
    assert extract_bearer_token({"authorization": "bearer xyz"}) == "xyz"


def test_extract_bearer_token_invalid():
    assert extract_bearer_token({}) is None
    assert extract_bearer_token({"authorization": "Basic abc"}) is None
    assert extract_bearer_token({"authorization": ""}) is None


def test_register_and_authenticate():
    reg = CredentialRegistry()
    reg.register("token-abc", "alice", "tenant-1", scopes={"read", "mutate"})

    auth = reg.authenticate("token-abc")
    assert auth is not None
    assert auth.principal.principal_id == "alice"
    assert auth.principal.tenant == "tenant-1"
    assert auth.principal.scopes == {"read", "mutate"}


def test_authenticate_unknown_token():
    reg = CredentialRegistry()
    assert reg.authenticate("unknown") is None


def test_authenticate_revoked_token():
    reg = CredentialRegistry()
    reg.register("token-abc", "alice", "tenant-1")
    reg.revoke("token-abc")
    assert reg.authenticate("token-abc") is None


def test_authenticate_expired_token():
    reg = CredentialRegistry()
    reg.register("token-abc", "alice", "tenant-1", expires_at=time.time() - 3600)
    assert reg.authenticate("token-abc") is None


def test_authenticate_non_expired_token():
    reg = CredentialRegistry()
    reg.register("token-abc", "alice", "tenant-1", expires_at=time.time() + 3600)
    assert reg.authenticate("token-abc") is not None


def test_rotate_token():
    reg = CredentialRegistry()
    reg.register("old-token", "alice", "tenant-1", scopes={"read"})
    new_entry = reg.rotate("old-token", "new-token")
    assert new_entry is not None
    assert new_entry.principal_id == "alice"
    assert reg.authenticate("old-token") is None
    assert reg.authenticate("new-token") is not None


def test_rotate_revoked_token_fails():
    reg = CredentialRegistry()
    reg.register("old-token", "alice", "tenant-1")
    reg.revoke("old-token")
    assert reg.rotate("old-token", "new-token") is None


def test_last_used_updated():
    reg = CredentialRegistry()
    reg.register("token-abc", "alice", "tenant-1")
    auth1 = reg.authenticate("token-abc")
    assert auth1.last_used_at is not None
    time.sleep(0.001)
    auth2 = reg.authenticate("token-abc")
    assert auth2.last_used_at >= auth1.last_used_at


def test_token_not_in_registry_plaintext():
    reg = CredentialRegistry()
    reg.register("my-secret-token-123", "alice", "tenant-1")
    entry = reg.get_entry("my-secret-token-123")
    assert entry is not None
    assert entry.token_hash.startswith("sha256:")
    assert "my-secret-token-123" not in entry.token_hash