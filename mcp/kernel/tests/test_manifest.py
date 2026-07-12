"""Unit tests for TransactionManifest."""

import os
import tempfile

from katana_kernel.manifest import TransactionManifest


def test_manifest_record_and_commit():
    d = tempfile.mkdtemp()
    m = TransactionManifest(os.path.join(d, ".katana", "manifests"))
    rec = m.record("memory", "create", {"id": "m-test01", "name": "test"}, {"committed": True})
    assert rec["domain"] == "memory"
    assert rec["op"] == "create"
    staged = [f for f in os.listdir(m.staging_dir) if f.endswith(".json")]
    assert len(staged) == 1

    m.commit_manifests()
    committed = [f for f in os.listdir(m.manifests_dir) if f.endswith(".json")]
    assert len(committed) == 1
    assert len([f for f in os.listdir(m.staging_dir) if f.endswith(".json")]) == 0


def test_manifest_rollback_cleans_staging():
    d = tempfile.mkdtemp()
    m = TransactionManifest(os.path.join(d, ".katana", "manifests"))
    m.record("memory", "create", {"id": "m-test01", "name": "test"})
    m.record("memory", "update", {"id": "m-test02", "name": "test2"})
    assert len([f for f in os.listdir(m.staging_dir) if f.endswith(".json")]) == 2
    m.rollback_staging()
    assert len([f for f in os.listdir(m.staging_dir) if f.endswith(".json")]) == 0


def test_manifest_list():
    d = tempfile.mkdtemp()
    m = TransactionManifest(os.path.join(d, ".katana", "manifests"))
    m.record("memory", "create", {"id": "m-a", "name": "a"})
    m.commit_manifests()
    m.record("memory", "update", {"id": "m-b", "name": "b"})
    m.commit_manifests()
    manifests = m.list_manifests()
    assert len(manifests) == 2


def test_manifest_get():
    d = tempfile.mkdtemp()
    m = TransactionManifest(os.path.join(d, ".katana", "manifests"))
    rec = m.record("memory", "create", {"id": "m-test", "name": "test"})
    m.commit_manifests()
    got = m.get_manifest(rec["manifest_id"])
    assert got is not None
    assert got["domain"] == "memory"


def test_manifest_get_nonexistent():
    d = tempfile.mkdtemp()
    m = TransactionManifest(os.path.join(d, ".katana", "manifests"))
    assert m.get_manifest("nonexistent") is None


def test_manifest_rollback_committed():
    d = tempfile.mkdtemp()
    m = TransactionManifest(os.path.join(d, ".katana", "manifests"))
    rec = m.record("memory", "create", {"id": "m-x", "name": "x"})
    m.commit_manifests()
    fname = f"{rec['manifest_id']}.json"
    assert os.path.exists(os.path.join(m.manifests_dir, fname))
    m.rollback_committed([rec["manifest_id"]])
    assert not os.path.exists(os.path.join(m.manifests_dir, fname))
    assert os.path.exists(os.path.join(m.staging_dir, fname))