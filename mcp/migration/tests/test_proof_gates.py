"""Contract tests for migration proof-gate suite (M3c).

Exercises every gate's PASS and controllable FAIL paths on controlled
fixtures (temp dirs only — no production data roots)."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from katana_migration.proof_gates import (
    hash_gate,
    history_gate,
    id_gate,
    idempotency_gate,
    integrity_gate,
    parity_gate,
    reference_gate,
    run_all_gates,
    _guard_no_production_paths,
    _evidence_digest,
)


# ── Fixtures (re-use rehearsal pattern) ───────────────────────────────────────

@pytest.fixture
def source_root(tmp_path):
    root = tmp_path / "source"
    root.mkdir()

    mem_dir = root / "data" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "alice").mkdir()
    (mem_dir / "alice" / "card1.md").write_text(
        "---\nid: m-a1b2c3\nname: card-one\ndescription: desc one\nstatus: active\nlast_verified: 2026-07-08\n---\n\n## Fact\nContent A\n",
        encoding="utf-8",
    )
    (mem_dir / "alice" / "card2.md").write_text(
        "---\nid: m-d4e5f6\nname: card-two\ndescription: desc two\nstatus: active\nlast_verified: 2026-07-08\n---\n\n## Fact\nContent B\n",
        encoding="utf-8",
    )
    (mem_dir / "bob").mkdir(exist_ok=True)
    (mem_dir / "bob" / "card3.md").write_text(
        "---\nid: m-789abc\nname: card-three\ndescription: desc three\nstatus: active\nlast_verified: 2026-07-08\n---\n\n## Fact\nContent C\n",
        encoding="utf-8",
    )

    legacy_dir = root / "data" / "vault" / "memory"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy-card.md").write_text(
        "---\nname: legacy-one\ndescription: legacy desc\n---\n\n## Fact\nLegacy content\n",
        encoding="utf-8",
    )

    wiki_dir = root / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "Zettelkasten").mkdir()
    (wiki_dir / "Zettelkasten" / "note1.md").write_text(
        "# Zettelkasten Note\n\nRef: [[m-a1b2c3]] and [[m-zzzzzz]]\n",
        encoding="utf-8",
    )
    (wiki_dir / "Zettelkasten" / "note2.md").write_text(
        "# Zettelkasten Note 2\n\nSee also [[note1]]\n",
        encoding="utf-8",
    )
    (wiki_dir / "WIKI.md").write_text(
        "---\nid: w-000001\ntitle: WIKI Schema\n---\n\n# WIKI Schema\n",
        encoding="utf-8",
    )

    wf_dir = root / "work-records"
    wf_dir.mkdir(parents=True)
    (wf_dir / "rec1.md").write_text(
        "# Work Record\n\nRecord content\n",
        encoding="utf-8",
    )

    exc_dir = root / "exceptions"
    exc_dir.mkdir(parents=True)
    (exc_dir / "binary.bin").write_bytes(b"\x00\x01\x02\x03\xFF\xFE")
    exe_path = exc_dir / "executable.sh"
    exe_path.write_text("#!/bin/bash\necho hello\n", encoding="utf-8")
    exe_path.chmod(0o755)
    (exc_dir / "lfs-pointer.md").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc123def456789\nsize 1234\n",
        encoding="utf-8",
    )
    try:
        symlink_path = exc_dir / "symlink.md"
        symlink_path.symlink_to(exc_dir / "binary.bin")
    except OSError:
        pass
    (exc_dir / "not-nfc.md").write_text(
        "Caf\u0065\u0301\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture
def source_sets_config(source_root):
    return [
        {
            "name": "memory_canonical",
            "root": str(source_root / "data" / "memory"),
            "source_repo": "/data/memory",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "memory_canonical",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "memory_legacy",
            "root": str(source_root / "data" / "vault" / "memory"),
            "source_repo": "/data/vault/memory",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "memory_legacy",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "id_backfill",
            "include": ["**/*.md"],
        },
        {
            "name": "wiki",
            "root": str(source_root / "wiki"),
            "source_repo": "/data/wiki",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "wiki",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "preserve",
            "auto_classify": True,
            "include": ["**/*.md"],
        },
        {
            "name": "work_folder",
            "root": str(source_root / "work-records"),
            "source_repo": "/data/work-records",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "work_folder",
            "prefix": "wf-",
            "destination_repo": "/data/work-records",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "exceptions",
            "root": str(source_root / "exceptions"),
            "source_repo": "/data/exceptions",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "unknown",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*"],
        },
    ]


@pytest.fixture
def manifest(source_sets_config):
    from katana_migration.inventory import run_inventory
    return run_inventory(source_sets_config, migration_run_id="test-proof-001")


@pytest.fixture
def dest_root(manifest, tmp_path):
    from katana_migration.rehearsal import run_rehearsal
    dest = tmp_path / "dest"
    dest.mkdir()
    run_rehearsal(manifest, str(dest), committer_date="2026-01-01T00:00:00+0000")
    return dest


# ── Production root guard ─────────────────────────────────────────────────────

def test_guard_no_production_paths_passes(tmp_path):
    _guard_no_production_paths(str(tmp_path / "safe"))


def test_guard_no_production_paths_rejects_production():
    with pytest.raises(RuntimeError, match="Production-root guard"):
        _guard_no_production_paths("/data/memory")
    with pytest.raises(RuntimeError, match="Production-root guard"):
        _guard_no_production_paths("/data/vault")
    with pytest.raises(RuntimeError, match="Production-root guard"):
        _guard_no_production_paths("/data/wiki")
    with pytest.raises(RuntimeError, match="Production-root guard"):
        _guard_no_production_paths("/data/work-records")


# ── Evidence digest determinism ───────────────────────────────────────────────

def test_evidence_digest_deterministic():
    d1 = _evidence_digest({"a": 1, "b": [2, 3]})
    d2 = _evidence_digest({"b": [2, 3], "a": 1})
    assert d1 == d2


# ── Parity gate PASS ──────────────────────────────────────────────────────────

def test_parity_gate_pass(manifest, dest_root):
    record = parity_gate(manifest, str(dest_root))
    assert record["status"] == "PASS"
    assert "evidence_digest" in record
    assert record["evidence_digest"].startswith("sha256:")


def test_parity_gate_deterministic(manifest, dest_root):
    r1 = parity_gate(manifest, str(dest_root))
    r2 = parity_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── Parity gate FAIL ──────────────────────────────────────────────────────────

def test_parity_gate_fail_unclassified_nonzero(manifest, dest_root):
    manifest["summary"]["unclassified"] = 5
    record = parity_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("unclassified" in f["check"] for f in record["failures"])


def test_parity_gate_fail_invariant_violation(manifest, dest_root):
    manifest["summary"]["tracked"] = 999
    record = parity_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("invariant" in f["check"] for f in record["failures"])


def test_parity_gate_fail_extra_objects(manifest, dest_root):
    mem_dest = dest_root / "data" / "memory"
    (mem_dest / "extra_file.md").write_text("# Extra\n", encoding="utf-8")
    record = parity_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("extra_objects" in f["check"] for f in record["failures"])


def test_parity_gate_fail_missing_object(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "preserve" and obj["destination_path"] == "alice/card1.md":
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            full.unlink()
            break
    record = parity_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("missing_objects" in f["check"] for f in record["failures"])


def test_parity_gate_fail_rejected_materialized(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "reject":
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text("# materialized\n", encoding="utf-8")
            break
    record = parity_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("rejected_materialized" in f["check"] for f in record["failures"])


# ── Hash gate PASS ────────────────────────────────────────────────────────────

def test_hash_gate_pass(manifest, dest_root):
    record = hash_gate(manifest, str(dest_root))
    assert record["status"] == "PASS"


def test_hash_gate_deterministic(manifest, dest_root):
    r1 = hash_gate(manifest, str(dest_root))
    r2 = hash_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── Hash gate FAIL ────────────────────────────────────────────────────────────

def test_hash_gate_fail_preserve_sha256_mismatch(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "preserve" and obj["destination_path"] == "alice/card1.md":
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            full.write_text("# tampered\n", encoding="utf-8")
            break
    record = hash_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("preserve_sha256_mismatch" in f["check"] for f in record["failures"])


def test_hash_gate_fail_id_backfill_body_altered(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "id_backfill":
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            dest_text = full.read_text()
            dest_text = dest_text.replace("Legacy content", "ALTERED content")
            full.write_text(dest_text, encoding="utf-8")
            break
    record = hash_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("id_backfill_body_altered" in f["check"] or "id_backfill_id_not_injected" in f["check"] for f in record["failures"])


def test_hash_gate_fail_normalize_missing_diff_manifest(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "norm_test.md").write_text(
        "---\ndescription: test\ntitle: norm test\n---\n\n# Norm\n",
        encoding="utf-8",
    )
    config = [{
        "name": "norm_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "normalize",
        "include": ["norm_test.md"],
    }]
    m = run_inventory(config, migration_run_id="norm-test")
    dest = tmp_path / "norm_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    diff_path = dest / "data" / "wiki" / "norm_test.md.diff_manifest.json"
    if diff_path.exists():
        diff_path.unlink()

    record = hash_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("missing_diff_manifest" in f["check"] for f in record["failures"])


def test_hash_gate_fail_normalize_post_hash_mismatch(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "posthash_test.md").write_text(
        "---\ndescription: test\ntitle: posthash test\n---\n\n# PostHash\n",
        encoding="utf-8",
    )
    config = [{
        "name": "posthash_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "normalize",
        "include": ["posthash_test.md"],
    }]
    m = run_inventory(config, migration_run_id="posthash-test")
    dest = tmp_path / "posthash_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    for obj in m["objects"]:
        if obj["destination_path"] == "posthash_test.md":
            obj["post_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
            break

    record = hash_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("post_hash_mismatch" in f["check"] for f in record["failures"])


# ── ID gate PASS ──────────────────────────────────────────────────────────────

def test_id_gate_pass(manifest, dest_root):
    record = id_gate(manifest, str(dest_root))
    assert record["status"] == "PASS"


def test_id_gate_deterministic(manifest, dest_root):
    r1 = id_gate(manifest, str(dest_root))
    r2 = id_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── ID gate FAIL ──────────────────────────────────────────────────────────────

def test_id_gate_fail_rejected_id_reused(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "reject":
            rejected_id = obj["domain_resource_id"]
            break
    else:
        pytest.skip("No rejected object with ID")

    for obj in manifest["objects"]:
        if obj["action"] == "preserve" and obj["domain_resource_id"]:
            obj["domain_resource_id"] = rejected_id
            break

    record = id_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("rejected_id_reused" in f["check"] for f in record["failures"])


def test_id_gate_fail_id_not_injected(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "id_backfill":
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            content = full.read_text()
            content = content.replace(obj["domain_resource_id"], "m-000000")
            full.write_text(content, encoding="utf-8")
            break

    record = id_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("id_not_injected" in f["check"] for f in record["failures"])


def test_id_gate_fail_canonical_id_missing(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "preserve" and obj["domain_resource_id"]:
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            content = full.read_text()
            content = content.replace(obj["domain_resource_id"], "m-000000")
            full.write_text(content, encoding="utf-8")
            break

    record = id_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("canonical_id_missing" in f["check"] for f in record["failures"])


# ── Reference gate PASS ───────────────────────────────────────────────────────

def test_reference_gate_pass(manifest, dest_root):
    record = reference_gate(manifest, str(dest_root))
    assert record["status"] == "PASS"


def test_reference_gate_deterministic(manifest, dest_root):
    r1 = reference_gate(manifest, str(dest_root))
    r2 = reference_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── Reference gate FAIL ───────────────────────────────────────────────────────

def test_reference_gate_fail_tampered_refs_json(manifest, dest_root):
    refs_path = dest_root / "data" / "wiki" / "references.json"
    if not refs_path.exists():
        refs_path = dest_root / "data" / "memory" / "references.json"
    if not refs_path.exists():
        pytest.skip("No references.json found")

    refs = json.loads(refs_path.read_text())
    refs["new_broken"] = refs.get("old_broken_acknowledged", 0) + 5
    refs_path.write_text(json.dumps(refs))

    record = reference_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("reference_constraint_violation" in f["check"] for f in record["failures"])


def test_reference_gate_fail_inject_new_broken(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    wiki_source = None
    mem_source = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            wiki_source = Path(ss["root"])
        if ss["name"] == "memory_canonical":
            mem_source = Path(ss["root"])
    if wiki_source is None:
        pytest.skip("No wiki source root")
    if mem_source is None:
        pytest.skip("No memory source root")

    existing_id = "m-a1b2c3"
    (wiki_source / "ref_break_test.md").write_text(
        f"---\nid: w-bbbb01\ntitle: Ref Break Test\nname: Ref Break Test\ndescription: test\n---\n\n# Ref\n\nSee [[{existing_id}]]\n",
        encoding="utf-8",
    )

    config = [
        {
            "name": "ref_break",
            "root": str(wiki_source),
            "source_repo": "/data/wiki",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "wiki_writable",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "preserve",
            "include": ["ref_break_test.md"],
        },
        {
            "name": "ref_break_mem",
            "root": str(mem_source),
            "source_repo": "/data/memory",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "memory_canonical",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
    ]
    m = run_inventory(config, migration_run_id="ref-break-test")

    dest = tmp_path / "ref_break_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    refs_path = dest / "data" / "wiki" / "references.json"
    assert refs_path.exists()
    refs = json.loads(refs_path.read_text())

    entries = refs.get("entries", [])
    for e in entries:
        if e.get("old_target_id") == existing_id:
            e["new_target_id"] = None
            e["disposition"] = "broken_new"
            break

    refs["new_broken"] = sum(1 for e in entries if e.get("disposition") == "broken_new")
    refs_path.write_text(json.dumps(refs))

    record = reference_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("reference_constraint_violation" in f["check"] for f in record["failures"])


# ── Integrity gate PASS ───────────────────────────────────────────────────────

def test_integrity_gate_pass(manifest, dest_root):
    record = integrity_gate(manifest, str(dest_root))
    assert record["status"] == "PASS"


def test_integrity_gate_deterministic(manifest, dest_root):
    r1 = integrity_gate(manifest, str(dest_root))
    r2 = integrity_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── Integrity gate FAIL ───────────────────────────────────────────────────────

def test_integrity_gate_fail_binary_content(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "binary_test.md").write_text("# binary\n", encoding="utf-8")
    config = [{
        "name": "binary_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["binary_test.md"],
    }]
    m = run_inventory(config, migration_run_id="binary-test")
    dest = tmp_path / "binary_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    target = dest / "data" / "wiki" / "binary_test.md"
    target.write_bytes(b"\x00\x01\x02\x03")

    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("binary_content" in f["check"] for f in record["failures"])


def test_integrity_gate_fail_lfs_pointer(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "lfs_test.md").write_text("# lfs\n", encoding="utf-8")
    config = [{
        "name": "lfs_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["lfs_test.md"],
    }]
    m = run_inventory(config, migration_run_id="lfs-test")
    dest = tmp_path / "lfs_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    target = dest / "data" / "wiki" / "lfs_test.md"
    target.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc123\tsize 1234\n",
        encoding="utf-8",
    )

    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("lfs_pointer" in f["check"] for f in record["failures"])


def test_integrity_gate_fail_symlink_rejected(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "symlink_target.md").write_text("# target\n", encoding="utf-8")
    config = [{
        "name": "symlink_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["symlink_target.md"],
    }]
    m = run_inventory(config, migration_run_id="symlink-test")
    dest = tmp_path / "symlink_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    target = dest / "data" / "wiki" / "symlink_target.md"
    target.unlink()
    symlink_path = dest / "data" / "wiki" / "extra_symlink.md"
    try:
        symlink_path.symlink_to("/dev/null")
    except OSError:
        pytest.skip("Cannot create symlink")

    m2 = run_inventory(config, migration_run_id="symlink-test2")
    m2["objects"].append({
        "migration_run_id": "symlink-test2",
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "source_path": "extra_symlink.md",
        "destination_repo": "/data/wiki",
        "destination_path": "extra_symlink.md",
        "action": "preserve",
        "domain_resource_id": "w-sym001",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "pre_hash": "0000000000000000000000000000000000000000000000000000000000000000",
        "post_hash": "0000000000000000000000000000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "size": 0,
        "file_mode": "120000",
        "git_blob_oid": None,
        "lfs_oid": None,
        "allowed_transformations": [],
        "reference_rewrites": [],
        "exception_code": None,
        "reason": None,
        "vfs_node_id": "w-sym001",
    })

    record = integrity_gate(m2, str(dest))
    assert record["status"] == "FAIL"
    assert any("symlink_rejected" in f["check"] for f in record["failures"])


def test_integrity_gate_fail_unicode_nfc(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "nfc_test.md").write_text("# nfc\n", encoding="utf-8")
    config = [{
        "name": "nfc_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["nfc_test.md"],
    }]
    m = run_inventory(config, migration_run_id="nfc-test")
    dest = tmp_path / "nfc_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    target = dest / "data" / "wiki" / "nfc_test.md"
    target.write_text("Caf\u0065\u0301\n", encoding="utf-8")

    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("unicode_nfc" in f["check"] for f in record["failures"])


def test_integrity_gate_fail_path_length(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    long_name = "a" * 260
    file_path = source_root / long_name
    try:
        file_path.write_text("# long path\n", encoding="utf-8")
    except OSError:
        pytest.skip("Filesystem does not support long filenames")

    config = [{
        "name": "pathlen_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": [long_name],
    }]
    m = run_inventory(config, migration_run_id="pathlen-test")
    dest = tmp_path / "pathlen_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("path_length" in f["check"] for f in record["failures"])


def test_integrity_gate_fail_casefold_collision(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import RehearsalEngine

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "Note.md").write_text("# Note\n", encoding="utf-8")
    (source_root / "note.md").write_text("# note\n", encoding="utf-8")
    config = [{
        "name": "casefold_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["Note.md", "note.md"],
    }]
    m = run_inventory(config, migration_run_id="casefold-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"

    dest = tmp_path / "casefold_dest"
    dest.mkdir()
    engine = RehearsalEngine(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    domain_groups = engine._group_by_domain(m["objects"])
    for dest_repo, domain_objects in domain_groups.items():
        dest_path = dest / dest_repo.lstrip("/")
        dest_path.mkdir(parents=True, exist_ok=True)
        engine._init_dest_repo_empty(dest_path)
        engine._materialize_objects(dest_path, domain_objects)

    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("casefold_collision" in f["check"] for f in record["failures"])


def test_integrity_gate_fail_executable_bit(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "exec_test.md").write_text("# exec\n", encoding="utf-8")
    config = [{
        "name": "exec_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["exec_test.md"],
    }]
    m = run_inventory(config, migration_run_id="exec-test")
    dest = tmp_path / "exec_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    target = dest / "data" / "wiki" / "exec_test.md"
    target.chmod(0o755)

    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("executable_bit" in f["check"] for f in record["failures"])


# ── History gate PASS ─────────────────────────────────────────────────────────

def test_history_gate_pass(manifest, dest_root):
    record = history_gate(manifest, str(dest_root))
    assert record["status"] == "PASS"


def test_history_gate_deterministic(manifest, dest_root):
    r1 = history_gate(manifest, str(dest_root))
    r2 = history_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── History gate FAIL ─────────────────────────────────────────────────────────

def test_history_gate_fail_out_of_scope_leak(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "Zettelkasten" / "in_scope.md").write_text("# in scope\n", encoding="utf-8")
    config = [{
        "name": "history_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["Zettelkasten/in_scope.md"],
    }]
    m = run_inventory(config, migration_run_id="history-test")
    dest = tmp_path / "history_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    repo_path = dest / "data" / "wiki"
    (repo_path / "out_of_scope.md").write_text("# out of scope\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo_path), "add", "out_of_scope.md"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "commit", "-m", "leak test"],
        check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"},
    )

    record = history_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any("out_of_scope_leak" in f["check"] for f in record["failures"])


def test_history_gate_fail_content_mismatch(manifest, dest_root):
    for obj in manifest["objects"]:
        if obj["action"] == "preserve" and obj["destination_path"] == "alice/card1.md":
            full = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
            full.write_text("# tampered content\n", encoding="utf-8")
            break

    record = history_gate(manifest, str(dest_root))
    assert record["status"] == "FAIL"
    assert any("content_mismatch" in f["check"] for f in record["failures"])


# ── Idempotency gate PASS ─────────────────────────────────────────────────────

def test_idempotency_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "idem_dest2"
    dest2.mkdir()
    record = idempotency_gate(manifest, str(dest_root), second_dest_root=str(dest2))
    assert record["status"] == "PASS"


# ── Idempotency gate FAIL ─────────────────────────────────────────────────────

def test_idempotency_gate_fail_rerun_error(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "idemfail_dest2"
    dest2.write_text("")
    record = idempotency_gate(manifest, str(dest_root), second_dest_root=str(dest2))
    assert record["status"] == "FAIL"
    assert any("idempotency_rerun_failed" in f["check"] for f in record["failures"])


def test_idempotency_gate_fail_tree_differ(manifest, dest_root, tmp_path):
    import tempfile
    from katana_migration.rehearsal import run_rehearsal

    dest2 = tmp_path / "idemtree_dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for domain_repo, repo_path in _domain_repo_paths(dest2):
        if not repo_path.exists():
            continue
        tampered = repo_path / ".migration" / "tamper.txt"
        tampered.parent.mkdir(parents=True, exist_ok=True)
        tampered.write_text("tamper")
        subprocess.run(
            ["git", "-C", str(repo_path), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo_path), "commit", "-m", "tamper"],
            check=True, capture_output=True,
            env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
                 "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"},
        )
        break

    record = idempotency_gate(manifest, str(dest_root), second_dest_root=str(dest2))
    assert record["status"] == "FAIL"


def _domain_repo_paths(dest_root):
    from pathlib import Path as P
    result = {}
    for item in P(dest_root).iterdir():
        if item.is_dir() and (item / ".git").exists():
            result[item.name] = item
    return result


# ── Aggregate run_all_gates ───────────────────────────────────────────────────

def test_run_all_gates_pass(manifest, dest_root):
    report = run_all_gates(manifest, str(dest_root))
    assert report["overall"] == "PASS"
    assert len(report["gates"]) == 7
    assert "evidence_digest" in report
    for g in report["gates"]:
        assert g["status"] == "PASS"
        assert "evidence_digest" in g


def test_run_all_gates_deterministic(manifest, dest_root):
    r1 = run_all_gates(manifest, str(dest_root))
    r2 = run_all_gates(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_run_all_gates_fail_on_one_gate(manifest, dest_root):
    manifest["summary"]["unclassified"] = 5
    report = run_all_gates(manifest, str(dest_root))
    assert report["overall"] == "FAIL"
    assert any(g["status"] == "FAIL" for g in report["gates"])


def test_run_all_gates_rejects_production(tmp_path):
    with pytest.raises(RuntimeError, match="Production-root guard"):
        run_all_gates({}, "/data/memory")


# ── Verification record schema ────────────────────────────────────────────────

def test_verification_record_schema(manifest, dest_root):
    record = parity_gate(manifest, str(dest_root))
    required = {"gate", "status", "checked", "failures", "evidence_digest"}
    assert required.issubset(set(record.keys()))
    assert record["status"] in ("PASS", "FAIL")
    assert isinstance(record["checked"], list)
    assert isinstance(record["failures"], list)
    assert record["evidence_digest"].startswith("sha256:")


def test_aggregate_record_schema(manifest, dest_root):
    report = run_all_gates(manifest, str(dest_root))
    assert "overall" in report
    assert "gates" in report
    assert "evidence_digest" in report
    assert report["overall"] in ("PASS", "FAIL")
    assert len(report["gates"]) == 7
    for g in report["gates"]:
        assert "gate" in g
        assert "status" in g
        assert "checked" in g
        assert "failures" in g
        assert "evidence_digest" in g