"""Tests for audit logging."""
from katana_remote.audit import AuditLogger, AuditEntry, audit_log, sanitize


def test_audit_log_creates_entry():
    logger = AuditLogger()
    entry = audit_log(
        logger,
        principal_id="alice",
        tenant="tenant-1",
        domain="memory",
        scopes=["read", "mutate"],
        operation="memory_create",
        resource_ids=["m-abc123"],
        base_commit="sha123",
        resulting_commit="sha456",
        client_identity="test-client",
    )
    assert entry.principal_id == "alice"
    assert entry.tenant == "tenant-1"
    assert entry.operation == "memory_create"
    assert entry.result == "success"
    assert len(logger) == 1


def test_audit_log_error():
    logger = AuditLogger()
    entry = audit_log(
        logger,
        principal_id="alice",
        tenant="tenant-1",
        domain="memory",
        scopes=["read"],
        operation="memory_create",
        error="insufficient scope",
    )
    assert entry.result == "error"
    assert entry.error == "insufficient scope"


def test_sanitize_redacts_token():
    data = {"token": "secret-value", "operation": "read"}
    result = sanitize(data)
    assert result["token"] == "[REDACTED]"
    assert result["operation"] == "read"


def test_sanitize_redacts_authorization():
    data = {"authorization": "Bearer abc", "path": "/test"}
    result = sanitize(data)
    assert result["authorization"] == "[REDACTED]"
    assert result["path"] == "/test"


def test_sanitize_redacts_binary():
    data = {"body": b"binary data", "name": "test"}
    result = sanitize(data)
    assert result["body"] == "[REDACTED]"
    assert result["name"] == "test"


def test_sanitize_redacts_nested():
    data = {"outer": {"token": "nested-secret", "name": "test"}}
    result = sanitize(data)
    assert result["outer"]["token"] == "[REDACTED]"
    assert result["outer"]["name"] == "test"


def test_sanitize_preserves_non_sensitive():
    data = {"operation": "read", "path": "/test", "commit": "sha123"}
    result = sanitize(data)
    assert result["operation"] == "read"
    assert result["path"] == "/test"
    assert result["commit"] == "sha123"


def test_audit_query_filters():
    logger = AuditLogger()
    audit_log(logger, "alice", "tenant-1", "memory", ["read"], "fs_read")
    audit_log(logger, "alice", "tenant-1", "memory", ["mutate"], "fs_create")
    audit_log(logger, "bob", "tenant-2", "memory", ["read"], "fs_read")

    results = logger.query(principal="alice")
    assert len(results) == 2

    results = logger.query(tenant="tenant-2")
    assert len(results) == 1

    results = logger.query(operation="fs_create")
    assert len(results) == 1


def test_audit_query_limit():
    logger = AuditLogger()
    for i in range(10):
        audit_log(logger, "alice", "tenant-1", "memory", ["read"], f"op_{i}")

    results = logger.query(limit=5)
    assert len(results) == 5