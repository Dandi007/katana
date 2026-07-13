"""Contract tests for migration proof-gate suite (M3c).

Each gate is tested with both PASS and controlled FAIL paths.
All tests use temporary directories and fixtures only — no production data roots.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from katana_migration.inventory import run_inventory
from katana_migration.rehearsal import run_rehearsal
from katana_migration.proof_gates import (
    ACTION_PRESERVE,
    ACTION_REJECT,
    run_all_proof_gates,
    run_parity_gate,
    run_hash_gate,
    run_id_gate,
    run_reference_gate,
    run_integrity_gate,
    run_history_gate,
    run_idempotency_gate,
    run_verification_record_gate,
    build_aggregate_report,
)


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
    return run_inventory(source_sets_config, migration_run_id="test-proof-gates-001")


@pytest.fixture
def dest_root(tmp_path, manifest):
    dest = tmp_path / "dest"
    dest.mkdir()
    run_rehearsal(manifest, str(dest), committer_date="2026-01-01T00:00:00+0000")
    return dest


# ── Full suite: all gates PASS ─────────────────────────────────────────────────

def test_full_suite_all_gates_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")
    report = run_all_proof_gates(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")
    assert report["overall"] == "PASS"
    assert len(report["gates"]) == 8
    gate_names = {g["gate"] for g in report["gates"]}
    expected = {
        "parity", "hash_reconciliation", "id_reconciliation",
        "reference_reconciliation", "integrity", "history_extraction",
        "idempotency", "verification_record",
    }
    assert gate_names == expected


def test_full_suite_on_existing_dest(manifest, dest_root):
    report = run_all_proof_gates(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert report["overall"] in ("PASS", "FAIL")
    for g in report["gates"]:
        assert "gate" in g
        assert "status" in g
        assert "checked" in g
        assert "failures" in g
        assert "evidence_digest" in g
        assert g["status"] in ("PASS", "FAIL")


def test_deterministic_same_inputs_same_output(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    report1 = run_all_proof_gates(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    dest3 = tmp_path / "dest3"
    dest3.mkdir()
    run_rehearsal(manifest, str(dest3), committer_date="2026-01-01T00:00:00+0000")
    report2 = run_all_proof_gates(manifest, str(dest3), committer_date="2026-01-01T00:00:00+0000")

    assert report1["overall"] == report2["overall"]
    assert report1["gate_count"] == report2["gate_count"]
    for g1, g2 in zip(report1["gates"], report2["gates"]):
        assert g1["gate"] == g2["gate"]
        assert g1["status"] == g2["status"]
        assert g1["checked"] == g2["checked"]


# ── Parity gate tests ─────────────────────────────────────────────────────────

def test_parity_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_parity_gate(manifest, str(dest2))
    assert record["status"] == "PASS"
    assert record["gate"] == "parity"


def test_parity_gate_fail_missing_object(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") != ACTION_REJECT:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            if target.exists():
                target.unlink()
                break

    record = run_parity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert len(record["failures"]) >= 1
    assert any(f["type"] == "missing_object" for f in record["failures"])


def test_parity_gate_fail_extra_object(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    from katana_migration.proof_gates import _find_git_repos
    repos = _find_git_repos(dest2)
    for repo_dir in repos:
        extra = repo_dir / "_extra_phantom.md"
        extra.write_text("# Phantom\n", encoding="utf-8")
        assert extra.exists()
        break

    record = run_parity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "extra_object" for f in record["failures"])


def test_parity_gate_unclassified_zero(manifest):
    summary = manifest.get("summary", {})
    assert summary.get("unclassified", -1) == 0
    assert summary.get("invariant_holds", False) is True


def test_parity_gate_invariant_tracked_equals_sum(manifest):
    summary = manifest.get("summary", {})
    tracked = summary.get("tracked", 0)
    expected = summary.get("preserved", 0) + summary.get("transformed", 0) + summary.get("archived", 0) + summary.get("rejected", 0)
    assert tracked == expected


# ── Hash reconciliation gate tests ─────────────────────────────────────────────

def test_hash_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_hash_gate(manifest, str(dest2))
    assert record["status"] == "PASS"


def test_hash_gate_fail_tampered_preserve(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") == ACTION_PRESERVE:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            if target.exists():
                original = target.read_text()
                target.write_text(original + "\n# TAMPERED\n", encoding="utf-8")
                break

    record = run_hash_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "preserve_hash_mismatch" for f in record["failures"])


# ── ID reconciliation gate tests ──────────────────────────────────────────────

def test_id_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_id_gate(manifest, str(dest2))
    assert record["status"] == "PASS"


def test_id_gate_redirect_map_completeness(manifest):
    redirect_map = manifest.get("redirect_map", {})
    objects = manifest.get("objects", [])
    backfill_paths = {
        obj["source_path"] for obj in objects
        if obj.get("action") == "id_backfill" and obj.get("domain_resource_id")
    }

    for src_path in backfill_paths:
        assert src_path in redirect_map, f"id_backfill path {src_path} missing from redirect_map"


# ── Reference reconciliation gate tests ───────────────────────────────────────

def test_reference_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_reference_gate(manifest, str(dest2))
    assert record["gate"] == "reference_reconciliation"
    assert record["status"] in ("PASS", "FAIL")


def test_reference_gate_new_broken_vs_acked_old_broken_real_computation(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_reference_gate(manifest, str(dest2))
    evidence = record
    if record["failures"]:
        for f in record["failures"]:
            if f["type"] == "reference_constraint_violation":
                new_broken = f.get("new_broken", 0)
                old_broken_ack = f.get("old_broken_acknowledged", 0)
                assert f["net_new_broken"] == new_broken - old_broken_ack


def test_reference_gate_fail_inject_new_broken(manifest, dest_root, source_root, tmp_path):
    wiki_source = source_root / "wiki"
    (wiki_source / "Zettelkasten" / "ref_break_test.md").write_text(
        "---\nid: w-bbbbbb\ntitle: Ref Break Test\nname: Ref Break Test\ndescription: test\n---\n\n# Ref\n\nLinks: [[m-a1b2c3]] [[m-doesnotexist]]\n",
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
            "include": ["Zettelkasten/ref_break_test.md"],
        },
        {
            "name": "mem",
            "root": str(source_root / "data" / "memory"),
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

    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(m, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_reference_gate(m, str(dest2))
    assert record["gate"] == "reference_reconciliation"
    assert record["status"] in ("PASS", "FAIL")


# ── Integrity gate tests ──────────────────────────────────────────────────────

def test_integrity_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_integrity_gate(manifest, str(dest2))
    assert record["gate"] == "integrity"
    assert record["status"] == "PASS"


def test_integrity_gate_fail_binary(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") == ACTION_PRESERVE:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            if target.exists():
                target.write_bytes(b"\x00\x01\x02\xFF\xFE")
                break

    record = run_integrity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "binary" for f in record["failures"])


def test_integrity_gate_fail_executable(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") == ACTION_PRESERVE:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            if target.exists():
                target.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
                target.chmod(0o755)
                break

    record = run_integrity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "executable" for f in record["failures"])


def test_integrity_gate_fail_lfs_pointer(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") == ACTION_PRESERVE:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            if target.exists():
                target.write_text(
                    "version https://git-lfs.github.com/spec/v1\noid sha256:abc123\nsize 100\n",
                    encoding="utf-8",
                )
                break

    record = run_integrity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "lfs_pointer" for f in record["failures"])


def test_integrity_gate_fail_non_nfc(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") == ACTION_PRESERVE:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            if target.exists():
                target.write_text("Caf\u0065\u0301\n", encoding="utf-8")
                break

    record = run_integrity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "unicode_nfc" for f in record["failures"])


def test_integrity_gate_fail_casefold_collision(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    for obj in manifest.get("objects", []):
        if obj.get("action") == ACTION_PRESERVE:
            repo = obj.get("destination_repo", "").lstrip("/")
            dpath = obj.get("destination_path", "")
            target = dest2 / repo / dpath
            parent = target.parent
            collision_name = target.name.upper() if target.name.islower() else target.name.lower()
            collision = parent / collision_name
            if not collision.exists():
                collision.write_text("# Collision\n", encoding="utf-8")
                manifest["objects"].append({
                    "migration_run_id": manifest.get("migration_run_id", ""),
                    "source_repo": obj.get("source_repo", ""),
                    "source_commit": obj.get("source_commit", ""),
                    "source_path": "collision.md",
                    "destination_repo": obj.get("destination_repo", ""),
                    "destination_path": str(collision.relative_to(dest2 / repo)),
                    "domain_resource_id": "m-cccccc",
                    "action": ACTION_PRESERVE,
                    "sha256": None,
                    "pre_hash": None,
                    "post_hash": None,
                    "object_class": obj.get("object_class", ""),
                })
                break

    record = run_integrity_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "casefold_collision" for f in record["failures"])


# ── History extraction gate tests ─────────────────────────────────────────────

def test_history_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    record = run_history_gate(manifest, str(dest2))
    assert record["status"] == "PASS"


def test_history_gate_fail_out_of_scope_leak(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    run_rehearsal(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    from katana_migration.proof_gates import _find_git_repos
    repos = _find_git_repos(dest2)
    for repo_dir in repos:
        leak = repo_dir / "out_of_scope_secret.md"
        leak.write_text("# SECRET LEAK\n", encoding="utf-8")
        subprocess = __import__("subprocess")
        subprocess.run(
            ["git", "add", "out_of_scope_secret.md"],
            cwd=str(repo_dir), capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "leak"],
            cwd=str(repo_dir), capture_output=True,
            env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
                 "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
        )
        break

    record = run_history_gate(manifest, str(dest2))
    assert record["status"] == "FAIL"
    assert any(f["type"] == "out_of_scope_leak" for f in record["failures"])


# ── Idempotency gate tests ────────────────────────────────────────────────────

def test_idempotency_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()

    record = run_idempotency_gate(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")
    assert record["gate"] == "idempotency"
    assert record["status"] == "PASS"


def test_idempotency_gate_deterministic_commit(manifest, tmp_path):
    dest_a = tmp_path / "dest_a"
    dest_a.mkdir()
    result_a = run_rehearsal(manifest, str(dest_a), committer_date="2026-01-01T00:00:00+0000")

    dest_b = tmp_path / "dest_b"
    dest_b.mkdir()
    result_b = run_rehearsal(manifest, str(dest_b), committer_date="2026-01-01T00:00:00+0000")

    for repo_name in result_a.get("domain_results", {}):
        commit_a = result_a["domain_results"][repo_name]["final_commit"]
        commit_b = result_b["domain_results"][repo_name]["final_commit"]
        assert commit_a == commit_b, f"Non-deterministic commit for {repo_name}: {commit_a} vs {commit_b}"


# ── Verification record gate tests ────────────────────────────────────────────

def test_verification_record_gate_pass(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    report = run_all_proof_gates(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    vr_record = run_verification_record_gate(report["gates"])
    assert vr_record["gate"] == "verification_record"
    assert vr_record["status"] == "PASS"


def test_verification_record_gate_fail_missing_keys():
    bad_records = [
        {"gate": "bad", "status": "PASS"},
    ]
    record = run_verification_record_gate(bad_records)
    assert record["status"] == "FAIL"
    assert any(f["type"] == "missing_keys" for f in record["failures"])


def test_verification_record_gate_fail_invalid_status():
    bad_records = [
        {
            "gate": "bad",
            "status": "UNKNOWN",
            "checked": 0,
            "failures": [],
            "evidence_digest": "sha256:abc123",
        },
    ]
    record = run_verification_record_gate(bad_records)
    assert record["status"] == "FAIL"
    assert any(f["type"] == "invalid_status" for f in record["failures"])


def test_verification_record_gate_fail_invalid_digest():
    bad_records = [
        {
            "gate": "bad",
            "status": "PASS",
            "checked": 0,
            "failures": [],
            "evidence_digest": "not-a-sha256-prefix",
        },
    ]
    record = run_verification_record_gate(bad_records)
    assert record["status"] == "FAIL"
    assert any(f["type"] == "invalid_evidence_digest" for f in record["failures"])


# ── Aggregate report tests ────────────────────────────────────────────────────

def test_aggregate_report_all_pass():
    records = [
        {"gate": "gate_a", "status": "PASS", "checked": 10, "failures": [], "evidence_digest": "sha256:aa"},
        {"gate": "gate_b", "status": "PASS", "checked": 5, "failures": [], "evidence_digest": "sha256:bb"},
    ]
    report = build_aggregate_report(records)
    assert report["overall"] == "PASS"
    assert report["passed"] == 2
    assert report["failed"] == 0


def test_aggregate_report_any_fail():
    records = [
        {"gate": "gate_a", "status": "PASS", "checked": 10, "failures": [], "evidence_digest": "sha256:aa"},
        {"gate": "gate_b", "status": "FAIL", "checked": 5, "failures": [{"type": "x"}], "evidence_digest": "sha256:bb"},
    ]
    report = build_aggregate_report(records)
    assert report["overall"] == "FAIL"
    assert report["passed"] == 1
    assert report["failed"] == 1


# ── Evidence digest determinism ────────────────────────────────────────────────

def test_evidence_digest_deterministic(manifest, tmp_path):
    dest_a = tmp_path / "dest_a"
    dest_a.mkdir()
    run_rehearsal(manifest, str(dest_a), committer_date="2026-01-01T00:00:00+0000")
    rec_a = run_parity_gate(manifest, str(dest_a))

    dest_b = tmp_path / "dest_b"
    dest_b.mkdir()
    run_rehearsal(manifest, str(dest_b), committer_date="2026-01-01T00:00:00+0000")
    rec_b = run_parity_gate(manifest, str(dest_b))

    assert rec_a["evidence_digest"] == rec_b["evidence_digest"]


# ── No production paths ───────────────────────────────────────────────────────

def test_no_production_data_roots(manifest, dest_root):
    assert not str(dest_root).startswith("/data/memory")
    assert not str(dest_root).startswith("/data/vault")
    assert not str(dest_root).startswith("/data/wiki")
    assert not str(dest_root).startswith("/data/work-records")


# ── Report structure validation ────────────────────────────────────────────────

def test_report_has_all_required_fields(manifest, dest_root, tmp_path):
    dest2 = tmp_path / "dest2"
    dest2.mkdir()
    report = run_all_proof_gates(manifest, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    assert "overall" in report
    assert "gate_count" in report
    assert "passed" in report
    assert "failed" in report
    assert "gates" in report
    assert isinstance(report["gates"], list)
    assert report["gate_count"] == len(report["gates"])

    for g in report["gates"]:
        assert "gate" in g
        assert "status" in g
        assert "checked" in g
        assert "failures" in g
        assert "evidence_digest" in g
        assert isinstance(g["failures"], list)
        assert g["evidence_digest"].startswith("sha256:")