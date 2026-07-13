"""Contract tests for migration inventory (M3a INVENTORIED phase)."""

import json

import pytest

from katana_migration.inventory import (
    ACTION_PRESERVE,
    ACTION_REJECT,
    EXC_BINARY,
    EXC_CASEFOLD_COLLISION,
    EXC_DUPLICATE_ID,
    EXC_EXECUTABLE,
    EXC_LFS_POINTER,
    EXC_MISSING_BRIEF,
    EXC_SYMLINK,
    EXC_YAML_PARSE,
    build_manifest,
    compute_summary,
    run_inventory,
)


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
    (mem_dir / "bob").mkdir()
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
        "# Zettelkasten Note\n\nSome content\n",
        encoding="utf-8",
    )
    (wiki_dir / "转换文档").mkdir()
    (wiki_dir / "转换文档" / "raw1.md").write_text(
        "# Raw Document\n\nRaw content\n",
        encoding="utf-8",
    )
    (wiki_dir / "DeepThought").mkdir()
    (wiki_dir / "DeepThought" / "topic1").mkdir()
    (wiki_dir / "DeepThought" / "topic1" / "report.md").write_text(
        "# DeepThought Report\n\nReport content\n",
        encoding="utf-8",
    )
    (wiki_dir / "findings.md").write_text(
        "# Findings\n\nFindings content\n",
        encoding="utf-8",
    )
    (wiki_dir / "WIKI.md").write_text(
        "# WIKI Schema\n\nSchema content\n",
        encoding="utf-8",
    )
    (wiki_dir / "log.md").write_text(
        "# Log\n\nLog content\n",
        encoding="utf-8",
    )
    (wiki_dir / "inbox").mkdir()
    (wiki_dir / "inbox" / "draft.md").write_text(
        "# Draft\n\nDraft content\n",
        encoding="utf-8",
    )

    wf_dir = root / "智元工作" / "工作记录"
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
    (exc_dir / "bad-yaml.md").write_text(
        "---\nkey: \"unclosed\n---\n\nBad YAML\n",
        encoding="utf-8",
    )
    (exc_dir / "missing-fields.md").write_text(
        "---\nid: m-111111\n---\n\nMissing fields\n",
        encoding="utf-8",
    )
    symlink_path = exc_dir / "symlink.md"
    try:
        symlink_path.symlink_to(exc_dir / "binary.bin")
    except OSError:
        pass

    return root


@pytest.fixture
def source_sets_config(source_root):
    return [
        {
            "name": "memory_canonical",
            "root": str(source_root / "data" / "memory"),
            "source_repo": "/data/memory",
            "source_commit": "c72e012a69a60e9448667d0b52a794db4f8b33aa",
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
            "source_commit": "c72e012a69a60e9448667d0b52a794db4f8b33aa",
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
            "source_commit": "c72e012a69a60e9448667d0b52a794db4f8b33aa",
            "object_class": "wiki",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "preserve",
            "auto_classify": True,
            "include": ["**/*.md"],
        },
        {
            "name": "work_folder",
            "root": str(source_root / "智元工作" / "工作记录"),
            "source_repo": "/data/work-records",
            "source_commit": "c72e012a69a60e9448667d0b52a794db4f8b33aa",
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
            "source_commit": "c72e012a69a60e9448667d0b52a794db4f8b33aa",
            "object_class": "unknown",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*"],
        },
    ]


@pytest.fixture
def manifest(source_sets_config):
    return run_inventory(source_sets_config, migration_run_id="test-run-001")


# ── Source set classification ─────────────────────────────────────────────────

def test_source_set_classification(manifest):
    classes = {r["object_class"] for r in manifest["objects"]}
    expected = {
        "memory_canonical",
        "memory_legacy",
        "wiki_writable",
        "wiki_raw",
        "wiki_schema",
        "work_folder",
        "unknown",
    }
    assert classes == expected, f"Unexpected object classes: {classes}"

    memory_canonical = [r for r in manifest["objects"] if r["object_class"] == "memory_canonical"]
    assert len(memory_canonical) == 3, f"Expected 3 canonical memory cards, got {len(memory_canonical)}"

    memory_legacy = [r for r in manifest["objects"] if r["object_class"] == "memory_legacy"]
    assert len(memory_legacy) == 1, f"Expected 1 legacy memory card, got {len(memory_legacy)}"

    wiki_writable = [r for r in manifest["objects"] if r["object_class"] == "wiki_writable"]
    assert len(wiki_writable) == 1, f"Expected 1 wiki writable, got {len(wiki_writable)}"

    wiki_schema = [r for r in manifest["objects"] if r["object_class"] == "wiki_schema"]
    assert len(wiki_schema) == 3, f"Expected 3 wiki schema items, got {len(wiki_schema)}"

    work_folder = [r for r in manifest["objects"] if r["object_class"] == "work_folder"]
    assert len(work_folder) == 1, f"Expected 1 work folder, got {len(work_folder)}"


# ── Manifest fields ───────────────────────────────────────────────────────────

_REQUIRED_FIELDS = [
    "migration_run_id",
    "source_repo",
    "source_commit",
    "source_path",
    "git_blob_oid",
    "sha256",
    "size",
    "file_mode",
    "lfs_oid",
    "object_class",
    "destination_repo",
    "destination_path",
    "domain_resource_id",
    "vfs_node_id",
    "action",
    "pre_hash",
    "post_hash",
    "allowed_transformations",
    "reference_rewrites",
    "exception_code",
    "reason",
]


def test_manifest_fields_complete(manifest):
    for obj in manifest["objects"]:
        for field in _REQUIRED_FIELDS:
            assert field in obj, f"Missing field '{field}' in object {obj.get('source_path', '?')}"


def test_manifest_top_level_fields(manifest):
    assert "migration_run_id" in manifest
    assert "source_sets" in manifest
    assert "objects" in manifest
    assert "summary" in manifest
    assert manifest["migration_run_id"] == "test-run-001"


# ── Invariant: tracked = preserved + transformed + archived + rejected ─────────

def test_invariant_holds(manifest):
    s = manifest["summary"]
    computed = s["preserved"] + s["transformed"] + s["archived"] + s["rejected"]
    assert s["tracked"] == computed, (
        f"Invariant broken: tracked={s['tracked']} != {computed} "
        f"(preserved={s['preserved']} + transformed={s['transformed']} "
        f"+ archived={s['archived']} + rejected={s['rejected']})"
    )


def test_unclassified_is_zero(manifest):
    assert manifest["summary"]["unclassified"] == 0, (
        f"Unclassified objects: {manifest['summary']['unclassified']}"
    )
    assert manifest["summary"]["invariant_holds"] is True


# ── Canonical Memory ID byte-for-byte preserved ───────────────────────────────

def test_canonical_memory_id_preserved(manifest):
    canonical = [r for r in manifest["objects"] if r["object_class"] == "memory_canonical"]
    id_map = {r["source_path"]: r["domain_resource_id"] for r in canonical}

    assert id_map.get("alice/card1.md") == "m-a1b2c3", (
        f"Expected m-a1b2c3, got {id_map.get('alice/card1.md')}"
    )
    assert id_map.get("alice/card2.md") == "m-d4e5f6", (
        f"Expected m-d4e5f6, got {id_map.get('alice/card2.md')}"
    )
    assert id_map.get("bob/card3.md") == "m-789abc", (
        f"Expected m-789abc, got {id_map.get('bob/card3.md')}"
    )


# ── Deterministic ID for legacy/no-ID objects ──────────────────────────────────

def test_legacy_id_deterministic(manifest):
    legacy = [r for r in manifest["objects"] if r["object_class"] == "memory_legacy"]
    assert len(legacy) == 1
    legacy_obj = legacy[0]
    assert legacy_obj["domain_resource_id"].startswith("m-")
    assert len(legacy_obj["domain_resource_id"]) == 8
    assert legacy_obj["action"] == "id_backfill"


def test_wiki_ids_deterministic(manifest):
    wiki_objs = [r for r in manifest["objects"] if r["object_class"].startswith("wiki_")]
    for obj in wiki_objs:
        assert obj["domain_resource_id"].startswith("w-"), (
            f"Expected w- prefix, got {obj['domain_resource_id']} for {obj['source_path']}"
        )
        assert len(obj["domain_resource_id"]) == 8


def test_work_folder_ids_deterministic(manifest):
    wf_objs = [r for r in manifest["objects"] if r["object_class"] == "work_folder"]
    for obj in wf_objs:
        assert obj["domain_resource_id"].startswith("wf-"), (
            f"Expected wf- prefix, got {obj['domain_resource_id']} for {obj['source_path']}"
        )
        assert len(obj["domain_resource_id"]) == 9


# ── Byte-identical manifest on repeated runs ──────────────────────────────────

def test_manifest_byte_identical(source_sets_config):
    m1 = run_inventory(source_sets_config, migration_run_id="test-run-001")
    m2 = run_inventory(source_sets_config, migration_run_id="test-run-001")
    assert m1 == m2, "Manifests are not byte-identical on repeated runs"


def test_manifest_json_byte_identical(source_sets_config):
    m1 = json.dumps(
        run_inventory(source_sets_config, migration_run_id="test-run-001"),
        indent=2, sort_keys=True, ensure_ascii=False,
    )
    m2 = json.dumps(
        run_inventory(source_sets_config, migration_run_id="test-run-001"),
        indent=2, sort_keys=True, ensure_ascii=False,
    )
    assert m1 == m2, "JSON manifests are not byte-identical on repeated runs"


# ── Exception handling ────────────────────────────────────────────────────────

def test_exception_codes_present(manifest):
    rejected = [r for r in manifest["objects"] if r["action"] == ACTION_REJECT]
    for obj in rejected:
        assert obj["exception_code"] is not None, (
            f"Rejected object {obj['source_path']} has no exception_code"
        )
        assert obj["reason"] is not None, (
            f"Rejected object {obj['source_path']} has no reason"
        )


def test_binary_file_rejected(manifest):
    binary = [r for r in manifest["objects"] if "binary" in r["source_path"].lower()]
    assert len(binary) >= 1
    for obj in binary:
        assert obj["exception_code"] == EXC_BINARY
        assert obj["action"] == ACTION_REJECT


def test_lfs_pointer_rejected(manifest):
    lfs = [r for r in manifest["objects"] if r["exception_code"] == EXC_LFS_POINTER]
    assert len(lfs) >= 1
    for obj in lfs:
        assert obj["action"] == ACTION_REJECT


def test_executable_rejected(manifest):
    executable = [r for r in manifest["objects"] if r["exception_code"] == EXC_EXECUTABLE]
    assert len(executable) >= 1
    for obj in executable:
        assert obj["action"] == ACTION_REJECT


def test_yaml_parse_error_rejected(manifest):
    yaml_err = [r for r in manifest["objects"] if r["exception_code"] == EXC_YAML_PARSE]
    assert len(yaml_err) >= 1
    for obj in yaml_err:
        assert obj["action"] == ACTION_REJECT


def test_missing_brief_rejected(manifest):
    missing = [r for r in manifest["objects"] if r["exception_code"] == EXC_MISSING_BRIEF]
    assert len(missing) >= 1
    for obj in missing:
        assert obj["action"] == ACTION_REJECT


def test_symlink_rejected(manifest):
    symlinks = [r for r in manifest["objects"] if r["exception_code"] in (EXC_SYMLINK, "CREDENTIAL_SYMLINK")]
    for obj in symlinks:
        assert obj["action"] == ACTION_REJECT


# ── Duplicate / collision detection ───────────────────────────────────────────

def test_duplicate_detection(tmp_path):
    root = tmp_path / "dup_test"
    root.mkdir()
    (root / "a.md").write_text("content A", encoding="utf-8")
    (root / "b.md").write_text("content A", encoding="utf-8")

    config = [{
        "name": "dup",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="dup-test")

    duplicate_ids = [r for r in manifest["objects"] if r["exception_code"] == EXC_DUPLICATE_ID]
    assert len(duplicate_ids) >= 1, "Duplicate IDs not detected for same-content files"


def test_casefold_collision_detection(tmp_path):
    root = tmp_path / "casefold_test"
    root.mkdir()
    (root / "Card.md").write_text("content 1", encoding="utf-8")
    (root / "card.md").write_text("content 2", encoding="utf-8")

    config = [{
        "name": "casefold",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="casefold-test")

    collisions = [r for r in manifest["objects"] if r["exception_code"] == EXC_CASEFOLD_COLLISION]
    assert len(collisions) >= 1, "Casefold collisions not detected"


# ── Schema-scope classification ───────────────────────────────────────────────

def test_schema_scope_classification(manifest):
    schema_objs = [r for r in manifest["objects"] if r["object_class"] == "wiki_schema"]
    schema_paths = {r["source_path"] for r in schema_objs}
    assert "WIKI.md" in schema_paths
    assert "log.md" in schema_paths
    assert "inbox/draft.md" in schema_paths


def test_raw_wiki_classification(manifest):
    raw_objs = [r for r in manifest["objects"] if r["object_class"] == "wiki_raw"]
    raw_paths = {r["source_path"] for r in raw_objs}
    assert "转换文档/raw1.md" in raw_paths
    assert "DeepThought/topic1/report.md" in raw_paths
    assert "findings.md" in raw_paths


# ── compute_summary unit test ─────────────────────────────────────────────────

def test_compute_summary():
    records = [
        {"action": "preserve", "source_path": "a.md"},
        {"action": "preserve", "source_path": "b.md"},
        {"action": "id_backfill", "source_path": "c.md"},
        {"action": "archive", "source_path": "d.md"},
        {"action": "reject", "source_path": "e.md"},
    ]
    summary = compute_summary(records)
    assert summary["tracked"] == 5
    assert summary["preserved"] == 2
    assert summary["transformed"] == 1
    assert summary["archived"] == 1
    assert summary["rejected"] == 1
    assert summary["unclassified"] == 0
    assert summary["invariant_holds"] is True


# ── build_manifest without run_id auto-generates one ──────────────────────────

def test_build_manifest_auto_run_id(source_sets_config):
    manifest = build_manifest(source_sets_config)
    assert manifest["migration_run_id"].startswith("mig-")
    assert len(manifest["migration_run_id"]) == 16


# ── No production data root touched ───────────────────────────────────────────

def test_no_production_paths(manifest):
    for obj in manifest["objects"]:
        src = obj["source_path"]
        assert not src.startswith("/data/memory"), f"Source path references production: {src}"
        assert not src.startswith("/data/vault/"), f"Source path references production: {src}"
        assert not src.startswith("/data/wiki"), f"Source path references production: {src}"
        assert not src.startswith("/data/work-records"), f"Source path references production: {src}"