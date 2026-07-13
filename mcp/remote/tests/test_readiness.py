"""Tests for readiness probes."""
from katana_remote.readiness import ReadinessService


def test_livez_default_ok():
    svc = ReadinessService()
    result = svc.livez()
    assert result["status"] == "ok"


def test_read_ready_default():
    svc = ReadinessService()
    result = svc.read_ready()
    assert result["status"] == "ok"
    assert result["auth_registry"] == "available"
    assert result["canonical_readable"] is True


def test_write_ready_default():
    svc = ReadinessService()
    result = svc.write_ready()
    assert result["status"] == "ok"
    assert result["writer_fence"] is True
    assert result["mutation_allowed"] is True


def test_write_ready_false_on_writer_fence_loss():
    svc = ReadinessService()
    svc.set_writer_fence(False)
    result = svc.write_ready()
    assert result["status"] == "not_ready"
    assert result["writer_fence"] is False
    assert result["mutation_allowed"] is False


def test_write_ready_false_on_unsupported_schema():
    svc = ReadinessService()
    svc.set_schema_compatible(False)
    result = svc.write_ready()
    assert result["status"] == "not_ready"
    assert result["schema_write_compat"] is False
    assert result["mutation_allowed"] is False


def test_write_ready_false_on_disk_reserve():
    svc = ReadinessService()
    svc.set_disk_reserve(False)
    result = svc.write_ready()
    assert result["status"] == "not_ready"
    assert result["disk_reserve"] == "low"
    assert result["mutation_allowed"] is False


def test_write_ready_false_on_reconciliation_not_done():
    svc = ReadinessService()
    svc.set_reconciliation_done(False)
    result = svc.write_ready()
    assert result["status"] == "not_ready"
    assert result["reconciliation"] == "in_progress"
    assert result["mutation_allowed"] is False


def test_schema_incompat_affects_both():
    svc = ReadinessService()
    svc.set_schema_compatible(False)
    assert svc.read_ready()["status"] == "not_ready"
    assert svc.write_ready()["status"] == "not_ready"


def test_restore_write_ready():
    svc = ReadinessService()
    svc.set_writer_fence(False)
    assert svc.write_ready()["status"] == "not_ready"
    svc.set_writer_fence(True)
    assert svc.write_ready()["status"] == "ok"