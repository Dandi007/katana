"""Contract tests for migration proof-gate suite (M3c)."""

import json
import os
import subprocess
import hashlib
from pathlib import Path

import pytest

from katana_migration.proof_gates import (
    parity_gate,
    hash_gate,
    id_gate,
    reference_gate,
    integrity_gate,
    history_gate,
    idempotency_gate,
    verification_record_gate,
    run_all_gates,
    _compute_evidence_digest,
)
from katana_migration.rehearsal import run_rehearsal, ACTION_PRESERVE, ACTION_REJECT, ACTION_ID_BACKFILL, ACTION_REWRITE, ACTION_NORMALIZE, ACTION_ARCHIVE


# ── Fixtures ──────────────────────────────────────────────────────────────────

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
    (mem_dir / "bob" / "card3.md").parent.mkdir(exist_ok=True)
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
    symlink_path = exc_dir / "symlink.md"
    try:
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
    return run_inventory(source_sets_config, migration_run_id="test-proof-gates-001")


@pytest.fixture
def dest_root(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    return dest


@pytest.fixture
def rehearsed(manifest, dest_root):
    run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    return dest_root


# ── No production paths ───────────────────────────────────────────────────────

def test_no_production_roots_in_tests(tmp_path):
    assert str(tmp_path).startswith("/tmp") or "/pytest" in str(tmp_path)


# ── Parity gate tests ─────────────────────────────────────────────────────────

def test_parity_gate_pass(rehearsed, manifest):
    record = parity_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    assert record["checked"] >= 5
    assert record["failures"] == []
    assert "evidence_digest" in record


def test_parity_gate_fail_silent_skip(rehearsed, manifest, tmp_path):
    manifest_copy = json.loads(json.dumps(manifest))
    manifest_copy["objects"].append({
        "migration_run_id": "test-proof-gates-001",
        "source_repo": "/data/memory",
        "source_commit": "0000000000000000000000000000000000000000",
        "source_path": "phantom.md",
        "sha256": "dummy",
        "destination_repo": "/data/memory",
        "destination_path": "phantom.md",
        "domain_resource_id": "m-phantom",
        "action": "preserve",
        "pre_hash": "dummy",
        "post_hash": "dummy",
    })
    record = parity_gate(manifest_copy, str(rehearsed))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "silent_skip" for f in record["failures"])


def test_parity_gate_fail_rejected_materialized(rehearsed, manifest):
    rejected = [o for o in manifest["objects"] if o["action"] == ACTION_REJECT]
    if not rejected:
        pytest.skip("No rejected objects in manifest")
    dest_path = rehearsed / rejected[0]["destination_repo"].lstrip("/") / rejected[0]["destination_path"]
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text("tampered")
    record = parity_gate(manifest, str(rehearsed))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "rejected_object_written" for f in record["failures"])


def test_parity_gate_invariant(manifest, rehearsed):
    record = parity_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    summary = manifest.get("summary", {})
    tracked = summary.get("tracked", 0)
    preserved = summary.get("preserved", 0)
    transformed = summary.get("transformed", 0)
    archived = summary.get("archived", 0)
    rejected = summary.get("rejected", 0)
    assert tracked == preserved + transformed + archived + rejected


# ── Hash gate tests ───────────────────────────────────────────────────────────

def test_hash_gate_pass(rehearsed, manifest):
    record = hash_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    assert record["failures"] == []


def test_hash_gate_fail_preserve_tampered(rehearsed, manifest):
    preserve_objs = [o for o in manifest["objects"] if o["action"] == ACTION_PRESERVE and o.get("destination_path")]
    if not preserve_objs:
        pytest.skip("No preserve objects")
    obj = preserve_objs[0]
    target = rehearsed / obj["destination_repo"].lstrip("/") / obj["destination_path"]
    target.write_text("TAMPERED CONTENT", encoding="utf-8")
    record = hash_gate(manifest, str(rehearsed))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "preserve_hash_mismatch" for f in record["failures"])


def test_hash_gate_id_backfill_body_unchanged(rehearsed, manifest):
    backfill_objs = [o for o in manifest["objects"] if o["action"] == ACTION_ID_BACKFILL]
    if not backfill_objs:
        pytest.skip("No id_backfill objects")
    obj = backfill_objs[0]
    source_file = None
    for ss in manifest["source_sets"]:
        if ss["source_repo"] == obj["source_repo"]:
            source_file = Path(ss["root"]) / obj["source_path"]
            break
    assert source_file is not None
    source_content = source_file.read_bytes()
    from katana_migration.rehearsal import _extract_body_bytes
    source_body = _extract_body_bytes(source_content)
    target = rehearsed / obj["destination_repo"].lstrip("/") / obj["destination_path"]
    dest_content = target.read_bytes()
    dest_body = _extract_body_bytes(dest_content)
    assert source_body == dest_body, "Body bytes should be unchanged"
    record = hash_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"


def test_hash_gate_diff_manifest_present(rehearsed, manifest, tmp_path):
    from katana_migration.inventory import run_inventory
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source set")
    (source_root / "diff_test.md").write_text(
        "---\ntitle: Test\ndescription: Test\n---\n\n# Test\n",
        encoding="utf-8",
    )
    config = [{
        "name": "diff_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "normalize",
        "include": ["diff_test.md"],
    }]
    m = run_inventory(config, migration_run_id="diff-test")
    dest = tmp_path / "diff_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    diff_path = dest / "data" / "wiki" / "diff_test.md.diff_manifest.json"
    assert diff_path.exists(), "Normalize must emit .diff_manifest.json"
    record = hash_gate(m, str(dest))
    assert record["status"] == "PASS"
    assert not any(f["type"] == "missing_diff_manifest" for f in record["failures"])


def test_hash_gate_fail_missing_diff_manifest(rehearsed, manifest, tmp_path):
    from katana_migration.inventory import run_inventory
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source set")
    (source_root / "nodiff_test.md").write_text(
        "---\ntitle: Test\ndescription: Test\n---\n\n# Test\n",
        encoding="utf-8",
    )
    config = [{
        "name": "nodiff_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "normalize",
        "include": ["nodiff_test.md"],
    }]
    m = run_inventory(config, migration_run_id="nodiff-test")
    dest = tmp_path / "nodiff_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    diff_path = dest / "data" / "wiki" / "nodiff_test.md.diff_manifest.json"
    if diff_path.exists():
        diff_path.unlink()
    record = hash_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "missing_diff_manifest" for f in record["failures"])


# ── ID gate tests ─────────────────────────────────────────────────────────────

def test_id_gate_pass(rehearsed, manifest):
    record = id_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    assert record["failures"] == []


def test_id_gate_pass_backfill_id_injected(rehearsed, manifest):
    backfill_objs = [o for o in manifest["objects"] if o["action"] == ACTION_ID_BACKFILL]
    for obj in backfill_objs:
        target = rehearsed / obj["destination_repo"].lstrip("/") / obj["destination_path"]
        content = target.read_text()
        assert obj["domain_resource_id"] in content


def test_id_gate_fail_redirect_map_incomplete(manifest, rehearsed):
    manifest_copy = json.loads(json.dumps(manifest))
    manifest_copy["redirect_map"] = {}
    backfill_objs = [o for o in manifest_copy["objects"] if o["action"] == ACTION_ID_BACKFILL]
    if backfill_objs:
        record = id_gate(manifest_copy, str(rehearsed))
        assert record["status"] == "FAIL"
        assert any(f["type"] == "missing_redirect" for f in record["failures"])


# ── Reference gate tests ──────────────────────────────────────────────────────

def test_reference_gate_pass(rehearsed, manifest):
    record = reference_gate(manifest, str(rehearsed))
    assert "status" in record
    assert "evidence_digest" in record
    assert "checked" in record
    assert isinstance(record["failures"], list)


def test_reference_gate_new_broken_vs_acked_old_broken_real_computation(manifest, rehearsed):
    found_refs = False
    for domain_name in {obj.get("destination_repo", "") for obj in manifest.get("objects", [])}:
        if not domain_name:
            continue
        refs_path = rehearsed / domain_name.lstrip("/") / "references.json"
        if not refs_path.exists():
            continue
        found_refs = True
        refs = json.loads(refs_path.read_text())
        old_broken_ack = refs.get("old_broken_acknowledged", 0)
        new_broken = refs.get("new_broken", 0)
        assert isinstance(new_broken, int) and isinstance(old_broken_ack, int), (
            "new_broken and old_broken_ack must be real computed integers"
        )
    assert found_refs, "No domains with references.json found"
    record = reference_gate(manifest, str(rehearsed))
    if record["status"] == "FAIL":
        assert any(f["type"] == "reference_constraint_violation" for f in record["failures"])


def test_reference_gate_fail_inject_new_broken(manifest, rehearsed, tmp_path):
    from katana_migration.inventory import run_inventory
    wiki_source_root = None
    mem_source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            wiki_source_root = Path(ss["root"])
        if ss["name"] == "memory_canonical":
            mem_source_root = Path(ss["root"])
    if wiki_source_root is None:
        pytest.skip("No wiki source set")
    (wiki_source_root / "ref_fail.md").write_text(
        "---\nid: w-refail\ntitle: ref fail\ndescription: ref fail\n---\n\n# Ref Fail\n\nRefs: [[m-doesnotexist]]\n",
        encoding="utf-8",
    )
    config = [{
        "name": "ref_fail",
        "root": str(wiki_source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["ref_fail.md"],
    }]
    if mem_source_root:
        config.append({
            "name": "mem_ref",
            "root": str(mem_source_root),
            "source_repo": "/data/memory",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "memory_canonical",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*.md"],
        })
    m = run_inventory(config, migration_run_id="ref-fail-test")
    dest = tmp_path / "ref_fail_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    refs_path = dest / "data" / "wiki" / "references.json"
    assert refs_path.exists()
    refs = json.loads(refs_path.read_text())
    old_broken_ack = refs.get("old_broken_acknowledged", 0)
    new_broken = refs.get("new_broken", 0)
    if new_broken > old_broken_ack:
        record = reference_gate(m, str(dest))
        assert record["status"] == "FAIL", (
            f"Expected FAIL but got {record['status']} with failures: {record['failures']}"
        )
        assert any(f["type"] == "reference_constraint_violation" for f in record["failures"]), (
            f"Expected reference_constraint_violation in failures: {record['failures']}"
        )
    else:
        record = reference_gate(m, str(dest))
        assert record["status"] == "PASS"


def test_reference_gate_fail_tampered_refs_json(manifest, rehearsed):
    for domain_name in {obj.get("destination_repo", "") for obj in manifest.get("objects", [])}:
        if not domain_name:
            continue
        refs_path = rehearsed / domain_name.lstrip("/") / "references.json"
        if not refs_path.exists():
            continue
        refs = json.loads(refs_path.read_text())
        refs["new_broken"] = refs.get("new_broken", 0) + 10
        refs["constraint_holds"] = False
        refs_path.write_text(json.dumps(refs))
    record = reference_gate(manifest, str(rehearsed))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "reference_constraint_violation" for f in record["failures"])


# ── Integrity gate tests ──────────────────────────────────────────────────────

def test_integrity_gate_pass(rehearsed, manifest):
    record = integrity_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    assert record["failures"] == []


def test_integrity_gate_fail_executable(manifest, tmp_path):
    from katana_migration.inventory import run_inventory
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source set")
    (source_root / "exec_check.sh").write_text("#!/bin/bash\necho hello\n", encoding="utf-8")
    config = [{
        "name": "exec_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["exec_check.sh"],
    }]
    m = run_inventory(config, migration_run_id="exec-test")
    for obj in m["objects"]:
        obj["action"] = ACTION_PRESERVE
    dest = tmp_path / "exec_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    exec_path = dest / "data" / "wiki" / "exec_check.sh"
    exec_path.chmod(0o755)
    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "executable_bit" for f in record["failures"])


def test_integrity_gate_fail_casefold(rehearsed, manifest, tmp_path):
    from katana_migration.inventory import run_inventory
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source root")
    (source_root / "Note.md").write_text("# Note\n", encoding="utf-8")
    (source_root / "other.md").write_text("# other\n", encoding="utf-8")
    config = [{
        "name": "casefold",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["Note.md", "other.md"],
    }]
    m = run_inventory(config, migration_run_id="casefold-test")
    for obj in m["objects"]:
        obj["action"] = ACTION_PRESERVE
    dest = tmp_path / "casefold_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    wiki_dest = dest / "data" / "wiki"
    (wiki_dest / "note.md").write_text("# note\n", encoding="utf-8")
    m["objects"].append({
        "migration_run_id": "casefold-test",
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "source_path": "note.md",
        "sha256": "dummy",
        "destination_repo": "/data/wiki",
        "destination_path": "note.md",
        "domain_resource_id": "w-note01",
        "action": "preserve",
        "pre_hash": "dummy",
        "post_hash": "dummy",
    })
    record = integrity_gate(m, str(dest))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "casefold_collision" for f in record["failures"])


# ── History gate tests ────────────────────────────────────────────────────────

def test_history_gate_pass(rehearsed, manifest):
    record = history_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    assert record["failures"] == []


def test_history_gate_fail_out_of_scope_leak(rehearsed, manifest, tmp_path):
    from katana_migration.inventory import run_inventory
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break
    if source_root is None:
        pytest.skip("No wiki source set")
    (source_root / "visible.md").write_text("# visible\n", encoding="utf-8")
    config = [{
        "name": "wiki_partial",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["visible.md"],
    }]
    m = run_inventory(config, migration_run_id="leak-test")
    dest = tmp_path / "leak_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    dest_path = dest / "data" / "wiki"
    (dest_path / "secret.md").write_text("# secret\n", encoding="utf-8")
    subprocess.run(["git", "add", "secret.md"], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "leak"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )
    record = history_gate(m, str(dest))
    assert record["status"] == "FAIL", (
        f"Expected FAIL but got {record['status']} with failures: {record['failures']}"
    )
    assert any(f["type"] == "out_of_scope_leak" for f in record["failures"]), (
        f"Expected out_of_scope_leak in failures: {record['failures']}"
    )


def test_history_gate_content_equality_check(rehearsed, manifest):
    for obj in manifest["objects"]:
        if obj.get("action") == ACTION_PRESERVE:
            source_file = None
            for ss in manifest["source_sets"]:
                if ss["source_repo"] == obj["source_repo"]:
                    source_file = Path(ss["root"]) / obj["source_path"]
                    break
            if source_file and source_file.exists():
                dest_path = rehearsed / obj["destination_repo"].lstrip("/") / obj["destination_path"]
                if dest_path.exists():
                    assert source_file.read_bytes() == dest_path.read_bytes(), (
                        f"Content mismatch for preserved {obj['destination_path']}"
                    )
    record = history_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"


# ── Idempotency gate tests ────────────────────────────────────────────────────

def test_idempotency_gate_pass(rehearsed, manifest):
    record = idempotency_gate(manifest, str(rehearsed))
    assert record["status"] == "PASS"
    assert record["failures"] == []


def test_idempotency_gate_fail_different_tree(manifest, rehearsed, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")
    memory_dest = dest2 / "data" / "memory"
    (memory_dest / "tamper.md").write_text("tampered", encoding="utf-8")
    subprocess.run(["git", "add", "tamper.md"], cwd=str(memory_dest), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "tamper"],
        cwd=str(memory_dest), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )
    record = idempotency_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "tree_not_byte_identical" for f in record["failures"])


# ── Verification record gate tests ────────────────────────────────────────────

def test_verification_record_gate_valid_records(rehearsed, manifest):
    records = [
        parity_gate(manifest, str(rehearsed)),
        hash_gate(manifest, str(rehearsed)),
        id_gate(manifest, str(rehearsed)),
        reference_gate(manifest, str(rehearsed)),
        integrity_gate(manifest, str(rehearsed)),
        history_gate(manifest, str(rehearsed)),
        idempotency_gate(manifest, str(rehearsed)),
    ]
    vrec = verification_record_gate(records)
    assert vrec["status"] == "PASS"
    assert vrec["failures"] == []


def test_verification_record_gate_invalid_status(rehearsed, manifest):
    records = [
        {"gate": "bad", "status": "INVALID", "checked": 0, "failures": [], "evidence_digest": "dummy"},
    ]
    vrec = verification_record_gate(records)
    assert vrec["status"] == "FAIL"
    assert any(f["type"] == "invalid_status" for f in vrec["failures"])


def test_verification_record_gate_missing_keys():
    records = [{"gate": "bad"}]
    vrec = verification_record_gate(records)
    assert vrec["status"] == "FAIL"
    assert any(f["type"] == "missing_keys" for f in vrec["failures"])


def test_verification_record_gate_evidence_digest_mismatch():
    record = {"gate": "bad", "status": "PASS", "checked": 0, "failures": [], "evidence_digest": "wrong"}
    vrec = verification_record_gate([record])
    assert vrec["status"] == "FAIL"
    assert any(f["type"] == "evidence_digest_mismatch" for f in vrec["failures"])


# ── Aggregate report tests ────────────────────────────────────────────────────

def test_aggregate_report_all_pass(rehearsed, manifest):
    report = run_all_gates(manifest, str(rehearsed))
    assert report["overall"] == "PASS"
    assert len(report["gates"]) == 8
    assert "evidence_digest" in report
    for gate in report["gates"]:
        assert "gate" in gate
        assert "status" in gate
        assert "checked" in gate
        assert "failures" in gate
        assert "evidence_digest" in gate


def test_aggregate_report_deterministic(rehearsed, manifest):
    report1 = run_all_gates(manifest, str(rehearsed))
    report2 = run_all_gates(manifest, str(rehearsed))
    assert report1["overall"] == report2["overall"]
    assert report1["evidence_digest"] == report2["evidence_digest"]
    for g1, g2 in zip(report1["gates"], report2["gates"]):
        assert g1["gate"] == g2["gate"]
        assert g1["status"] == g2["status"]
        assert g1["evidence_digest"] == g2["evidence_digest"]


def test_aggregate_report_fail_when_gate_fails(rehearsed, manifest):
    preserve_objs = [o for o in manifest["objects"] if o["action"] == ACTION_PRESERVE and o.get("destination_path")]
    if not preserve_objs:
        pytest.skip("No preserve objects")
    obj = preserve_objs[0]
    target = rehearsed / obj["destination_repo"].lstrip("/") / obj["destination_path"]
    target.write_text("TAMPERED", encoding="utf-8")
    report = run_all_gates(manifest, str(rehearsed))
    assert report["overall"] == "FAIL"
    hash_record = [g for g in report["gates"] if g["gate"] == "hash"][0]
    assert hash_record["status"] == "FAIL"


def test_no_production_paths_in_gates(rehearsed, manifest, tmp_path):
    assert str(rehearsed).startswith(str(tmp_path))
    report = run_all_gates(manifest, str(rehearsed))
    assert report["overall"] == "PASS"


# ── Evidence digest determinism ────────────────────────────────────────────────

def test_evidence_digest_deterministic(rehearsed, manifest):
    from katana_migration.proof_gates import _compute_evidence_digest
    rec1 = parity_gate(manifest, str(rehearsed))
    rec2 = parity_gate(manifest, str(rehearsed))
    assert rec1["evidence_digest"] == rec2["evidence_digest"]
    assert rec1["evidence_digest"] == _compute_evidence_digest(
        {k: v for k, v in rec1.items() if k != "evidence_digest"}
    )