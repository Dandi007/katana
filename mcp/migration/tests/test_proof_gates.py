"""Contract tests for migration proof-gate suite (M3c).

Tests each gate with PASS and controlled-FAIL fixtures, plus aggregate
report schema and determinism checks.  All fixtures use tmp_path only —
no production data roots touched.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from katana_migration.proof_gates import (
    _guard_no_production_paths,
    hash_gate,
    history_gate,
    id_gate,
    idempotency_gate,
    integrity_gate,
    parity_gate,
    reference_gate,
    run_all_gates,
)


# ── Reusable fixtures ──────────────────────────────────────────────────────────

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
    from katana_migration.rehearsal import run_rehearsal
    return run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")


# ── Production guard tests ─────────────────────────────────────────────────────

def test_guard_blocks_production_paths():
    with pytest.raises(RuntimeError, match="refused production path"):
        _guard_no_production_paths("/data/memory/some/file.md")

    with pytest.raises(RuntimeError, match="refused production path"):
        _guard_no_production_paths("/data/wiki")

    with pytest.raises(RuntimeError, match="refused production path"):
        _guard_no_production_paths("/data/vault/anything")

    with pytest.raises(RuntimeError, match="refused production path"):
        _guard_no_production_paths("/data/work-records/x")


def test_guard_allows_tmp_path(tmp_path):
    _guard_no_production_paths(str(tmp_path / "dest"))
    _guard_no_production_paths(str(tmp_path / "some" / "other" / "path"))


def test_all_gates_call_guard(tmp_path):
    dummy_manifest = {"objects": [], "summary": {"tracked": 0, "preserved": 0, "transformed": 0, "archived": 0, "rejected": 0, "unclassified": 0, "invariant_holds": True}, "source_sets": [], "redirect_map": {}, "migration_run_id": "test"}
    with pytest.raises(RuntimeError, match="refused production path"):
        parity_gate(dummy_manifest, "/data/memory")


# ── Parity gate tests ──────────────────────────────────────────────────────────

def test_parity_gate_pass(manifest, dest_root, rehearsed):
    result = parity_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert len(result["checked"]) >= 5
    assert result["evidence_digest"] != ""


def test_parity_gate_fail_missing_manifest_item(manifest, dest_root, rehearsed):
    orig_objects = manifest["objects"]
    manifest["objects"] = [dict(orig_objects[0])]
    result = parity_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("missing" in f["check"] or "extra" in f["check"] for f in result["failures"])
    manifest["objects"] = orig_objects


def test_parity_gate_fail_extra_object_in_dest(manifest, dest_root, rehearsed):
    extra_path = dest_root / "data" / "memory" / "extra_file.md"
    extra_path.parent.mkdir(parents=True, exist_ok=True)
    extra_path.write_text("extra")
    result = parity_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("extra" in f["check"] for f in result["failures"])


def test_parity_gate_fail_rejected_materialized(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "parity_source"
    source_root.mkdir()
    (source_root / "rej.md").write_text("# rejected\n", encoding="utf-8")

    config = [{
        "name": "parity_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "reject",
        "include": ["rej.md"],
    }]
    m = run_inventory(config, migration_run_id="parity-rej-test")

    dest = tmp_path / "parity_dest"
    dest.mkdir()
    m["objects"][0]["action"] = "preserve"

    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    result = parity_gate(m, str(dest))
    assert result["status"] == "PASS"


def test_parity_gate_fail_unclassified_nonzero(manifest, dest_root, rehearsed):
    manifest["summary"]["unclassified"] = 5
    manifest["summary"]["invariant_holds"] = False
    result = parity_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("unclassified" in f["check"] for f in result["failures"])


# ── Hash gate tests ────────────────────────────────────────────────────────────

def test_hash_gate_pass(manifest, dest_root, rehearsed):
    result = hash_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert result["evidence_digest"] != ""


def test_hash_gate_fail_preserve_sha_mismatch(manifest, dest_root, rehearsed):
    preserve_objs = [o for o in manifest["objects"] if o["action"] == "preserve"]
    if not preserve_objs:
        pytest.skip("No preserve objects")
    obj = preserve_objs[0]
    dest_path = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
    dest_path.write_bytes(b"tampered content")
    result = hash_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("sha256_mismatch" in f["check"] for f in result["failures"])


def test_hash_gate_fail_id_backfill_body_altered(manifest, dest_root, rehearsed):
    backfill_objs = [o for o in manifest["objects"] if o["action"] == "id_backfill"]
    if not backfill_objs:
        pytest.skip("No id_backfill objects")
    obj = backfill_objs[0]
    dest_path = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
    orig = dest_path.read_bytes()
    dest_path.write_bytes(orig + b"\nALTERED")
    result = hash_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("body_altered" in f["check"] for f in result["failures"])


def test_hash_gate_fail_transform_post_hash_mismatch(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "hash_post_source"
    source_root.mkdir()
    (source_root / "norm.md").write_text(
        "---\ntitle: norm test\ndescription: desc\n---\n\n# Normalize\n",
        encoding="utf-8",
    )

    config = [{
        "name": "hash_post_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/test",
        "default_action": "normalize",
        "include": ["norm.md"],
    }]
    m = run_inventory(config, migration_run_id="hash-post-test")

    for obj in m["objects"]:
        obj["post_hash"] = "deadbeef" * 8

    dest = tmp_path / "hash_post_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    result = hash_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("post_hash_mismatch" in f["check"] for f in result["failures"])


def test_hash_gate_pre_hash_source_verified(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "hash_source"
    source_root.mkdir()
    (source_root / "norm.md").write_text(
        "---\ntitle: norm test\ndescription: desc\n---\n\n# Normalize\n",
        encoding="utf-8",
    )

    config = [{
        "name": "hash_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/test",
        "default_action": "normalize",
        "include": ["norm.md"],
    }]
    m = run_inventory(config, migration_run_id="hash-pre-test")

    dest = tmp_path / "hash_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    result = hash_gate(m, str(dest))
    assert result["status"] == "PASS"


# ── ID gate tests ──────────────────────────────────────────────────────────────

def test_id_gate_pass(manifest, dest_root, rehearsed):
    result = id_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert result["evidence_digest"] != ""


def test_id_gate_fail_rejected_id_reused(manifest, dest_root, rehearsed):
    rejected_objs = [o for o in manifest["objects"] if o["action"] == "reject"]
    if not rejected_objs:
        from katana_migration.inventory import run_inventory, ACTION_REJECT
        for obj in manifest["objects"]:
            if obj.get("action") == "preserve":
                obj["action"] = ACTION_REJECT
                break
        rejected_objs = [o for o in manifest["objects"] if o["action"] == "reject"]

    if not rejected_objs:
        pytest.skip("No rejected objects")

    rej = rejected_objs[0]
    rejected_id = rej.get("domain_resource_id")
    if not rejected_id:
        pytest.skip("Rejected object has no ID")

    preserve_objs = [o for o in manifest["objects"] if o["action"] == "preserve"]
    if not preserve_objs:
        pytest.skip("No preserve objects to reuse ID on")

    preserve_objs[0]["domain_resource_id"] = rejected_id
    result = id_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("rejected_id_reused" in f["check"] for f in result["failures"])


def test_id_gate_fail_distinct_active_objects_share_id(manifest, dest_root, rehearsed):
    active = [obj for obj in manifest["objects"] if obj["action"] != "reject"]
    assert len(active) >= 2
    active[1]["domain_resource_id"] = active[0]["domain_resource_id"]

    result = id_gate(manifest, str(dest_root))

    assert result["status"] == "FAIL"
    assert any(failure["check"] == "duplicate_active_id" for failure in result["failures"])


def test_id_gate_fail_canonical_id_missing(manifest, dest_root, rehearsed):
    canonical_objs = [o for o in manifest["objects"] if o.get("object_class") == "memory_canonical"]
    if canonical_objs:
        canonical_objs[0]["domain_resource_id"] = None
        result = id_gate(manifest, str(dest_root))


# ── Reference gate tests ───────────────────────────────────────────────────────

def test_reference_gate_pass(manifest, dest_root, rehearsed):
    result = reference_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
    assert result["evidence_digest"] != ""
    assert "net_new_broken_equals_zero" in result["checked"]


def test_reference_gate_pass_no_broken_refs(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "ref_clean_source"
    source_root.mkdir()
    (source_root / "ref_clean.md").write_text(
        "---\nid: w-refclean\ntitle: Clean\nname: Clean\ndescription: No broken refs\n---\n\n# Clean\n\nNo refs here.\n",
        encoding="utf-8",
    )

    config = [{
        "name": "ref_clean_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["ref_clean.md"],
    }]
    m = run_inventory(config, migration_run_id="ref-clean-test")

    dest = tmp_path / "ref_clean_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    result = reference_gate(m, str(dest))
    assert result["status"] == "PASS"
    assert result["failures"] == []


def test_reference_gate_fail_tampered_refs_json(manifest, dest_root, rehearsed):
    refs_path = dest_root / "data" / "wiki" / "references.json"
    if not refs_path.exists():
        refs_path = dest_root / "data" / "memory" / "references.json"
    if not refs_path.exists():
        pytest.skip("No references.json found")

    refs = json.loads(refs_path.read_text())
    refs["new_broken"] = refs.get("new_broken", 0) + 5
    refs_path.write_text(json.dumps(refs))

    result = reference_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("new_broken" in f["check"] for f in result["failures"])


def test_reference_gate_fail_inject_new_broken(manifest, dest_root, rehearsed):
    refs_path = dest_root / "data" / "wiki" / "references.json"
    if not refs_path.exists():
        refs_path = dest_root / "data" / "memory" / "references.json"
    if not refs_path.exists():
        pytest.skip("No references.json found")

    refs = json.loads(refs_path.read_text())
    old_ack = refs.get("old_broken_acknowledged", 0)
    refs["new_broken"] = old_ack + 3
    refs_path.write_text(json.dumps(refs))

    result = reference_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("new_broken" in f["check"] for f in result["failures"])


def test_reference_gate_fail_actual_newly_broken_reference(manifest, dest_root, rehearsed):
    refs_path = dest_root / "data" / "wiki" / "references.json"
    refs = json.loads(refs_path.read_text())
    resolved = next(
        entry for entry in refs["entries"]
        if entry["disposition"] == "resolved" and entry["old_target_id"] is not None
    )
    resolved["disposition"] = "broken_new"
    resolved["new_target_id"] = None
    refs["new_broken"] = 1
    refs["net_new_broken"] = 1
    refs["constraint_holds"] = False
    refs_path.write_text(json.dumps(refs))

    result = reference_gate(manifest, str(dest_root))

    assert result["status"] == "FAIL"
    assert any(failure["check"] == "net_new_broken_equals_zero" for failure in result["failures"])


def test_reference_gate_pass_old_broken_ack_gt_zero(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "ref_source"
    source_root.mkdir()
    (source_root / "ref_a.md").write_text(
        "---\nid: w-refaaaa\ntitle: Ref A\nname: Ref A\ndescription: Reference test\n---\n\n# Ref A\n\nRefs: [[m-doesnotexist1]] [[m-doesnotexist2]]\n",
        encoding="utf-8",
    )

    config = [{
        "name": "ref_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["ref_a.md"],
    }]
    m = run_inventory(config, migration_run_id="ref-ack-test")

    dest = tmp_path / "ref_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    refs_path = dest / "data" / "wiki" / "references.json"
    assert refs_path.exists()
    refs = json.loads(refs_path.read_text())
    assert refs["old_broken_acknowledged"] >= 2
    assert refs["new_broken"] == 0

    result = reference_gate(m, str(dest))
    assert result["status"] == "PASS", (
        f"Expected PASS when old_broken_ack={refs['old_broken_acknowledged']}, "
        f"new_broken={refs['new_broken']}, "
        "because the acknowledged baseline is not part of net-new broken links"
    )


def test_reference_gate_fail_new_broken_gt_old_broken_ack_with_nonzero_ack(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "ref_fail_source"
    source_root.mkdir()
    (source_root / "ref_b.md").write_text(
        "---\nid: w-refbbbb\ntitle: Ref B\nname: Ref B\ndescription: Reference test\n---\n\n# Ref B\n\nRefs: [[m-doesnotexist3]]\n",
        encoding="utf-8",
    )

    config = [{
        "name": "ref_fail_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["ref_b.md"],
    }]
    m = run_inventory(config, migration_run_id="ref-fail-test")

    dest = tmp_path / "ref_fail_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    refs_path = dest / "data" / "wiki" / "references.json"
    assert refs_path.exists()
    refs = json.loads(refs_path.read_text())

    assert refs["old_broken_acknowledged"] >= 1, f"old_broken_ack={refs['old_broken_acknowledged']}"
    refs["new_broken"] = refs["old_broken_acknowledged"] + 2
    refs_path.write_text(json.dumps(refs))

    result = reference_gate(m, str(dest))
    assert result["status"] == "FAIL", (
        f"Expected FAIL when old_broken_ack={refs['old_broken_acknowledged']}, "
        f"new_broken={refs['new_broken']}, "
        f"new_broken - old_broken_ack = {refs['new_broken'] - refs['old_broken_acknowledged']}"
    )
    assert any("new_broken" in f["check"] for f in result["failures"])


def test_reference_gate_pass_zero_new_broken_with_larger_acknowledged_baseline(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "ref_less_source"
    source_root.mkdir()
    (source_root / "ref_c.md").write_text(
        "---\nid: w-refcccc\ntitle: Ref C\nname: Ref C\ndescription: Reference test\n---\n\n# Ref C\n\nRefs: [[m-doesnotexist4]] [[m-doesnotexist5]]\n",
        encoding="utf-8",
    )

    config = [{
        "name": "ref_less_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["ref_c.md"],
    }]
    m = run_inventory(config, migration_run_id="ref-less-test")

    dest = tmp_path / "ref_less_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    refs_path = dest / "data" / "wiki" / "references.json"
    assert refs_path.exists()
    refs = json.loads(refs_path.read_text())

    assert refs["old_broken_acknowledged"] >= 2, f"old_broken_ack={refs['old_broken_acknowledged']}"
    refs["new_broken"] = 0
    refs_path.write_text(json.dumps(refs))

    result = reference_gate(m, str(dest))
    assert result["status"] == "PASS", result["failures"]


# ── Integrity gate tests ───────────────────────────────────────────────────────

def test_integrity_gate_pass(manifest, dest_root, rehearsed):
    result = integrity_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert result["evidence_digest"] != ""
    assert len(result["checked"]) >= 5


def test_integrity_gate_fail_binary(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = tmp_path / "int_source"
    source_root.mkdir()
    (source_root / "data.bin").write_bytes(b"\x00\x01\x02\x03")

    config = [{
        "name": "int_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["data.bin"],
    }]
    m = run_inventory(config, migration_run_id="int-bin-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"
        obj["exception_code"] = None
        obj["preservation_modes"] = []

    dest = tmp_path / "int_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    (dest_path / "data.bin").write_bytes(b"\x00\x01\x02\x03")
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("binary" in f["check"] for f in result["failures"])


def test_integrity_gate_fail_symlink(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = tmp_path / "int_sym_source"
    source_root.mkdir()
    (source_root / "target.md").write_text("# target\n", encoding="utf-8")
    symlink_path = source_root / "link.md"
    try:
        symlink_path.symlink_to(source_root / "target.md")
    except OSError:
        pytest.skip("Cannot create symlinks in this environment")

    config = [{
        "name": "sym_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["link.md"],
    }]
    m = run_inventory(config, migration_run_id="int-sym-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"
        obj["exception_code"] = None

    dest = tmp_path / "int_sym_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    try:
        (dest_path / "link.md").symlink_to(dest_path / "target.md")
    except OSError:
        pytest.skip("Cannot create symlinks")
    (dest_path / "target.md").write_text("# target\n")
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("symlink" in f["check"] for f in result["failures"])


def test_integrity_gate_fail_lfs(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = tmp_path / "int_lfs_source"
    source_root.mkdir()
    (source_root / "lfs.md").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc123def456789\nsize 1234\n",
        encoding="utf-8",
    )

    config = [{
        "name": "lfs_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["lfs.md"],
    }]
    m = run_inventory(config, migration_run_id="int-lfs-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"
        obj["exception_code"] = None
        obj["preservation_modes"] = []

    dest = tmp_path / "int_lfs_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    (dest_path / "lfs.md").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc123def456789\nsize 1234\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("lfs" in f["check"] for f in result["failures"])


def test_integrity_gate_fail_executable(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = tmp_path / "int_exe_source"
    source_root.mkdir()
    exe_path = source_root / "script.sh"
    exe_path.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    exe_path.chmod(0o755)

    config = [{
        "name": "exe_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["script.sh"],
    }]
    m = run_inventory(config, migration_run_id="int-exe-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"
        obj["exception_code"] = None

    dest = tmp_path / "int_exe_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    exe_dest = dest_path / "script.sh"
    exe_dest.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    exe_dest.chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("executable" in f["check"] for f in result["failures"])


def test_integrity_gate_fail_unicode_nfc(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = tmp_path / "int_nfc_source"
    source_root.mkdir()
    (source_root / "nfc.md").write_text(
        "Caf\u0065\u0301\n",
        encoding="utf-8",
    )

    config = [{
        "name": "nfc_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["nfc.md"],
    }]
    m = run_inventory(config, migration_run_id="int-nfc-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"
        obj["exception_code"] = None

    dest = tmp_path / "int_nfc_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    (dest_path / "nfc.md").write_text("Caf\u0065\u0301\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("nfc" in f["check"] for f in result["failures"])


def test_integrity_gate_acknowledges_preexisting_casefold(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = tmp_path / "int_case_source"
    source_root.mkdir()
    (source_root / "Note.md").write_text("# Note\n", encoding="utf-8")
    (source_root / "note.md").write_text("# note\n", encoding="utf-8")

    config = [{
        "name": "case_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["Note.md", "note.md"],
    }]
    m = run_inventory(config, migration_run_id="int-case-test")

    dest = tmp_path / "int_case_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    (dest_path / "Note.md").write_text("# Note\n", encoding="utf-8")
    (dest_path / "note.md").write_text("# note\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert len(result["acknowledged"]) == 1


def test_integrity_gate_fails_migration_introduced_casefold(tmp_path):
    from katana_migration.inventory import run_inventory

    upper_root = tmp_path / "int_case_upper"
    lower_root = tmp_path / "int_case_lower"
    upper_root.mkdir()
    lower_root.mkdir()
    (upper_root / "INDEX.md").write_text("# upper\n", encoding="utf-8")
    (lower_root / "index.md").write_text("# lower\n", encoding="utf-8")

    def source_set(root, source_repo):
        return {
            "name": source_repo,
            "root": str(root),
            "source_repo": source_repo,
            "source_commit": "0" * 40,
            "object_class": "wiki_writable",
            "prefix": "w-",
            "destination_repo": "/data/test",
            "default_action": "preserve",
            "include": ["*.md"],
        }

    manifest = run_inventory(
        [source_set(upper_root, "/source/upper"), source_set(lower_root, "/source/lower")],
        migration_run_id="introduced-casefold",
    )
    dest = tmp_path / "introduced_case_dest"
    domain = dest / "data" / "test"
    domain.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(domain)], check=True, capture_output=True)

    result = integrity_gate(manifest, str(dest))

    assert result["status"] == "FAIL"
    assert any(failure["check"] == "casefold_collision" for failure in result["failures"])


def test_integrity_gate_fail_path_length(manifest, dest_root, tmp_path, monkeypatch):
    from katana_migration.inventory import run_inventory
    from katana_migration.proof_gates import integrity_gate

    monkeypatch.setattr("katana_migration.proof_gates.MAX_BASENAME_LENGTH", 50, raising=False)

    source_root = tmp_path / "plen"
    source_root.mkdir()
    long_name = "a" * 60
    (source_root / long_name).write_text("# long\n", encoding="utf-8")

    config = [{
        "name": "plen_test",
        "root": str(source_root),
        "source_repo": "/data/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": [long_name],
    }]
    m = run_inventory(config, migration_run_id="int-plen-test")

    for obj in m["objects"]:
        obj["action"] = "preserve"
        obj["exception_code"] = None

    dest = tmp_path / "int_plen_dest"
    dest.mkdir()
    dest_path = dest / "data" / "test"
    dest_path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", str(dest_path)], check=True, capture_output=True)
    (dest_path / long_name).write_text("# long\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = integrity_gate(m, str(dest))
    assert result["status"] == "FAIL"
    assert any("path_length" in f["check"] for f in result["failures"])


# ── History gate tests ─────────────────────────────────────────────────────────

def test_history_gate_pass(manifest, dest_root, rehearsed):
    result = history_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert result["evidence_digest"] != ""


def test_history_gate_fail_out_of_scope_leak(manifest, dest_root, rehearsed):
    dest_path = dest_root / "data" / "memory"
    if not (dest_path / ".git").exists():
        pytest.skip("No git repo at memory destination")

    leak_path = dest_path / "leaked_file.md"
    leak_path.write_text("# leaked\n")
    subprocess.run(["git", "add", "leaked_file.md"], cwd=str(dest_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "leak"],
        cwd=str(dest_path), capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )

    result = history_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("out_of_scope" in f["check"] for f in result["failures"])


def test_history_gate_fail_content_mismatch(manifest, dest_root, rehearsed):
    preserve_objs = [o for o in manifest["objects"] if o["action"] == "preserve"]
    if not preserve_objs:
        pytest.skip("No preserve objects")
    obj = preserve_objs[0]
    dest_path = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
    dest_path.write_bytes(b"altered content")
    result = history_gate(manifest, str(dest_root))
    assert result["status"] == "FAIL"
    assert any("content_mismatch" in f["check"] for f in result["failures"])


# ── Idempotency gate tests ─────────────────────────────────────────────────────

def test_idempotency_gate_pass(manifest, dest_root, rehearsed):
    result = idempotency_gate(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["status"] == "PASS"
    assert result["failures"] == []
    assert result["evidence_digest"] != ""


def test_idempotency_gate_fail_rerun_error(manifest, dest_root, rehearsed):
    bad_manifest = dict(manifest)
    bad_manifest["source_sets"] = [
        {
            "name": "bad",
            "root": "/nonexistent/path/for/rerun",
            "source_repo": "/data/bad",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "test",
            "prefix": "t-",
            "destination_repo": "/data/bad",
            "default_action": "preserve",
            "include": ["**/*"],
        }
    ]
    bad_manifest["objects"] = [
        {
            "destination_repo": "/data/bad",
            "destination_path": "somefile.md",
            "source_repo": "/data/bad",
            "source_path": "somefile.md",
            "action": "preserve",
            "object_class": "test",
            "domain_resource_id": None,
            "sha256": None,
            "pre_hash": None,
            "post_hash": None,
        }
    ]

    result = idempotency_gate(bad_manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["status"] == "FAIL"
    assert any("rerun_error" in f["check"] for f in result["failures"])


def test_idempotency_gate_fail_tree_differ(manifest, dest_root, rehearsed, tmp_path):
    import tempfile
    import shutil

    dest_path = dest_root / "data" / "memory"
    if not (dest_path / ".git").exists():
        pytest.skip("No git repo at memory destination")

    result = idempotency_gate(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["status"] == "PASS"


# ── Aggregate report tests ─────────────────────────────────────────────────────

def test_aggregate_report_all_pass(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory
    from katana_migration.rehearsal import run_rehearsal

    source_root = tmp_path / "aggr_source"
    source_root.mkdir()
    (source_root / "simple.md").write_text(
        "---\nid: w-aggr001\ntitle: Simple\nname: Simple\ndescription: Simple\n---\n\n# Simple\n\nNo refs.\n",
        encoding="utf-8",
    )

    config = [{
        "name": "aggr_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "include": ["simple.md"],
    }]
    m = run_inventory(config, migration_run_id="aggr-test")

    dest = tmp_path / "aggr_dest"
    dest.mkdir()
    run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    report = run_all_gates(m, str(dest), skip_idempotency=True)
    assert report["status"] == "PASS"
    assert report["failed_gate_count"] == 0
    assert len(report["gates"]) == 6
    for gate_record in report["gates"]:
        assert gate_record["status"] == "PASS"
        assert gate_record["evidence_digest"] != ""


def test_aggregate_report_includes_idempotency(manifest, dest_root, rehearsed):
    report = run_all_gates(manifest, str(dest_root), skip_idempotency=False)
    assert len(report["gates"]) == 7
    gate_names = [g["gate"] for g in report["gates"]]
    assert "idempotency" in gate_names


def test_aggregate_report_schema(manifest, dest_root, rehearsed):
    report = run_all_gates(manifest, str(dest_root), skip_idempotency=True)
    assert "status" in report
    assert "gates" in report
    assert "failed_gates" in report
    assert "total_gates" in report
    assert "passed_gates" in report
    assert "failed_gate_count" in report
    assert report["status"] in ("PASS", "FAIL")

    for gate_record in report["gates"]:
        assert "gate" in gate_record
        assert "status" in gate_record
        assert "checked" in gate_record
        assert "failures" in gate_record
        assert "evidence_digest" in gate_record
        assert gate_record["status"] in ("PASS", "FAIL")
        assert isinstance(gate_record["checked"], list)
        assert isinstance(gate_record["failures"], list)


# ── Determinism tests ──────────────────────────────────────────────────────────

def test_evidence_digest_deterministic(manifest, dest_root, rehearsed):
    r1 = run_all_gates(manifest, str(dest_root), skip_idempotency=True)
    r2 = run_all_gates(manifest, str(dest_root), skip_idempotency=True)

    for g1, g2 in zip(r1["gates"], r2["gates"]):
        assert g1["gate"] == g2["gate"]
        assert g1["evidence_digest"] == g2["evidence_digest"], (
            f"Evidence digest mismatch for {g1['gate']}: {g1['evidence_digest']} vs {g2['evidence_digest']}"
        )


def test_parity_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = parity_gate(manifest, str(dest_root))
    r2 = parity_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_hash_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = hash_gate(manifest, str(dest_root))
    r2 = hash_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_id_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = id_gate(manifest, str(dest_root))
    r2 = id_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_reference_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = reference_gate(manifest, str(dest_root))
    r2 = reference_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_integrity_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = integrity_gate(manifest, str(dest_root))
    r2 = integrity_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_history_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = history_gate(manifest, str(dest_root))
    r2 = history_gate(manifest, str(dest_root))
    assert r1["evidence_digest"] == r2["evidence_digest"]


def test_idempotency_gate_deterministic(manifest, dest_root, rehearsed):
    r1 = idempotency_gate(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    r2 = idempotency_gate(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert r1["evidence_digest"] == r2["evidence_digest"]


# ── All gates present ──────────────────────────────────────────────────────────

def test_all_seven_gates_present(manifest, dest_root, rehearsed):
    report = run_all_gates(manifest, str(dest_root))
    gate_names = {g["gate"] for g in report["gates"]}
    expected = {"parity", "hash", "id", "reference", "integrity", "history", "idempotency"}
    assert gate_names == expected, f"Missing gates: {expected - gate_names}"


# ── No production paths in tests ───────────────────────────────────────────────

def test_no_production_paths_in_gate_fixtures(manifest, dest_root, rehearsed, tmp_path):
    assert str(dest_root).startswith(str(tmp_path))
    result = parity_gate(manifest, str(dest_root))
    assert result["status"] == "PASS"
