"""Contract tests for migration inventory (M3a INVENTORIED phase)."""

import json

import pytest

from katana_migration.inventory import (
    ACTION_ID_BACKFILL,
    ACTION_PRESERVE,
    ACTION_REJECT,
    EXC_BINARY,
    EXC_CASEFOLD_COLLISION,
    EXC_DESTINATION_PATH_CONFLICT,
    EXC_DUPLICATE_BASENAME,
    EXC_DUPLICATE_ID,
    EXC_EXECUTABLE,
    EXC_LFS_POINTER,
    EXC_MISSING_BRIEF,
    EXC_PATH_LENGTH,
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
    assert "redirect_map" in manifest
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


def test_missing_brief_rejected(tmp_path):
    root = tmp_path / "memory"
    root.mkdir()
    (root / "missing-fields.md").write_text(
        "---\nid: m-111111\n---\n\nMissing fields\n",
        encoding="utf-8",
    )
    config = [{
        "name": "memory",
        "root": str(root),
        "source_repo": "/test/memory",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "memory_canonical",
        "prefix": "m-",
        "destination_repo": "/test/memory",
        "default_action": "preserve",
        "include": ["*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="missing-memory-fields")
    missing = [r for r in manifest["objects"] if r["exception_code"] == EXC_MISSING_BRIEF]
    assert len(missing) >= 1
    for obj in missing:
        assert obj["action"] == ACTION_REJECT


def test_symlink_rejected(manifest):
    symlinks = [r for r in manifest["objects"] if r["exception_code"] in (EXC_SYMLINK, "CREDENTIAL_SYMLINK")]
    assert len(symlinks) >= 1, "Symlink fixture not created — test is vacuous"
    for obj in symlinks:
        assert obj["action"] == ACTION_REJECT
        assert obj["sha256"] is None, "Symlink must not be dereferenced (sha256 must be None)"
        assert obj["git_blob_oid"] is None, "Symlink must not be dereferenced (git_blob_oid must be None)"
        assert obj["file_mode"] == "120000", f"Expected symlink mode 120000, got {obj['file_mode']}"


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
        "object_class": "memory_legacy",
        "prefix": "m-",
        "destination_repo": "/test",
        "default_action": "id_backfill",
        "include": ["*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="dup-test")

    duplicate_ids = [r for r in manifest["objects"] if r["exception_code"] == EXC_DUPLICATE_ID]
    assert len(duplicate_ids) >= 1, "Duplicate IDs not detected for same-content files"


def test_duplicate_basename_detection(tmp_path):
    root = tmp_path / "dup_basename_test"
    dir1 = root / "dir1"
    dir2 = root / "dir2"
    dir1.mkdir(parents=True)
    dir2.mkdir(parents=True)
    (dir1 / "note.md").write_text("content one", encoding="utf-8")
    (dir2 / "note.md").write_text("content two different", encoding="utf-8")

    config = [{
        "name": "dup_basename",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="dup-basename-test")

    dup_basenames = [r for r in manifest["objects"] if r["exception_code"] == EXC_DUPLICATE_BASENAME]
    assert len(dup_basenames) >= 1, "Duplicate basenames not detected"

    for obj in dup_basenames:
        assert obj["action"] == ACTION_REJECT
        assert "note.md" in obj["reason"]


def _work_folder_source_set(root, *, source_repo="/test/work-source", destination_repo="/test/work"):
    return {
        "name": source_repo,
        "root": str(root),
        "source_repo": source_repo,
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "work_folder",
        "prefix": "wf-",
        "destination_repo": destination_repo,
        "default_action": "preserve",
        "include": ["**/*"],
    }


def _write_work_folder(folder, *, resource_id=None):
    folder.mkdir(parents=True)
    id_line = f"id: {resource_id}\n" if resource_id else "id: legacy-folder-id\n"
    (folder / "_brief.md").write_text(
        f"---\n{id_line}title: Fixture\nstatus: active\n---\n\n**Goal:** Test\n",
        encoding="utf-8",
    )
    (folder / "progress.md").write_text("# Progress\n", encoding="utf-8")
    (folder / "context.md").write_text("# Context\n", encoding="utf-8")
    run_dir = folder / "runs" / "001" / "output"
    run_dir.mkdir(parents=True)
    (run_dir / "cmd.txt").write_text("command\n", encoding="utf-8")
    (run_dir / "err.txt").write_text("", encoding="utf-8")
    (run_dir / "meta.json").write_text('{"status": "ok"}\n', encoding="utf-8")


def test_real_scale_work_folders_use_path_identity_and_rehearse(tmp_path):
    root = tmp_path / "work-records"
    folder_count = 200
    for index in range(folder_count):
        _write_work_folder(root / "2026" / "07" / f"{index % 31 + 1:02d}" / f"task-{index:03d}")

    config = [_work_folder_source_set(root)]
    manifest = run_inventory(config, migration_run_id="work-folder-scale")
    repeated = run_inventory(config, migration_run_id="work-folder-scale")

    assert manifest == repeated
    assert manifest["summary"] == {
        "tracked": folder_count * 6,
        "preserved": folder_count * 6,
        "transformed": 0,
        "archived": 0,
        "rejected": 0,
        "unclassified": 0,
        "invariant_holds": True,
    }

    by_folder = {}
    for obj in manifest["objects"]:
        assert obj["action"] == ACTION_PRESERVE
        assert obj["destination_path"] == obj["source_path"]
        assert obj["exception_code"] is None
        by_folder.setdefault(obj["work_folder_path"], set()).add(obj["domain_resource_id"])

    assert len(by_folder) == folder_count
    assert all(len(ids) == 1 for ids in by_folder.values())
    folder_ids = {next(iter(ids)) for ids in by_folder.values()}
    assert len(folder_ids) == folder_count
    assert all(resource_id.startswith("wf-") and len(resource_id) == 9 for resource_id in folder_ids)

    from katana_migration.rehearsal import run_rehearsal

    result = run_rehearsal(
        manifest,
        str(tmp_path / "rehearsal"),
        committer_date="2026-01-01T00:00:00+0000",
    )
    assert result["invariant_holds"] is True


def test_missing_brief_is_applied_to_the_work_folder_only(tmp_path):
    root = tmp_path / "work-records"
    good = root / "2026" / "07" / "01" / "with-brief"
    missing = root / "2026" / "07" / "02" / "without-brief"
    _write_work_folder(good)
    missing.mkdir(parents=True)
    (missing / "progress.md").write_text("# Progress\n", encoding="utf-8")
    (missing / "context.md").write_text("# Context\n", encoding="utf-8")

    manifest = run_inventory([_work_folder_source_set(root)], migration_run_id="missing-brief")
    good_records = [obj for obj in manifest["objects"] if obj["work_folder_path"].endswith("with-brief")]
    missing_records = [obj for obj in manifest["objects"] if obj["work_folder_path"].endswith("without-brief")]

    assert good_records
    assert all(obj["action"] == ACTION_PRESERVE for obj in good_records)
    assert all(obj["exception_code"] is None for obj in good_records)
    assert missing_records
    assert all(obj["action"] == ACTION_REJECT for obj in missing_records)
    assert all(obj["exception_code"] == EXC_MISSING_BRIEF for obj in missing_records)
    assert not any(
        obj["source_path"].endswith("_brief.md") and obj["exception_code"] == EXC_MISSING_BRIEF
        for obj in manifest["objects"]
    )


def test_structure_preserving_wiki_raw_allows_repeated_basenames_and_content(tmp_path):
    root = tmp_path / "wiki"
    for directory in (root / "转换文档" / "a", root / "转换文档" / "b"):
        directory.mkdir(parents=True)
        (directory / "report.md").write_text("# Same report\n", encoding="utf-8")

    config = [{
        "name": "wiki",
        "root": str(root),
        "source_repo": "/test/wiki-source",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki",
        "prefix": "w-",
        "destination_repo": "/test/wiki",
        "default_action": "preserve",
        "auto_classify": True,
        "include": ["**/*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="wiki-raw-paths")

    assert manifest["summary"]["rejected"] == 0
    assert {obj["object_class"] for obj in manifest["objects"]} == {"wiki_raw"}
    assert len({obj["domain_resource_id"] for obj in manifest["objects"]}) == 2


def test_work_folder_duplicate_id_rejects_both_folders(tmp_path):
    root = tmp_path / "work-records"
    _write_work_folder(root / "2026" / "07" / "01" / "first", resource_id="wf-abc123")
    _write_work_folder(root / "2026" / "07" / "02" / "second", resource_id="wf-abc123")

    manifest = run_inventory([_work_folder_source_set(root)], migration_run_id="duplicate-folder-id")

    assert manifest["objects"]
    assert all(obj["action"] == ACTION_REJECT for obj in manifest["objects"])
    assert all(obj["exception_code"] == EXC_DUPLICATE_ID for obj in manifest["objects"])


def test_distinct_sources_mapping_to_same_destination_path_are_rejected(tmp_path):
    first_root = tmp_path / "first-source"
    second_root = tmp_path / "second-source"
    relative_folder = ("2026", "07", "01", "same-destination")
    _write_work_folder(first_root.joinpath(*relative_folder))
    _write_work_folder(second_root.joinpath(*relative_folder))

    config = [
        _work_folder_source_set(first_root, source_repo="/test/first"),
        _work_folder_source_set(second_root, source_repo="/test/second"),
    ]
    manifest = run_inventory(config, migration_run_id="destination-conflict")

    assert manifest["objects"]
    assert all(obj["action"] == ACTION_REJECT for obj in manifest["objects"])
    assert all(
        obj["exception_code"] == EXC_DESTINATION_PATH_CONFLICT
        for obj in manifest["objects"]
    )


def test_path_length_exception(tmp_path, monkeypatch):
    import katana_migration.inventory as inv_mod
    monkeypatch.setattr(inv_mod, "MAX_PATH_LENGTH", 100)

    root = tmp_path / "pathlen_test"
    root.mkdir()
    deep = root
    long_name = "d" * 50
    for i in range(3):
        deep = deep / long_name
        deep.mkdir()
    deep_file = deep / "file.md"
    deep_file.write_text("content", encoding="utf-8")

    config = [{
        "name": "pathlen",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="pathlen-test")

    path_len_objs = [r for r in manifest["objects"] if r["exception_code"] == EXC_PATH_LENGTH]
    assert len(path_len_objs) >= 1, "Path-length exception not detected"

    for obj in path_len_objs:
        assert obj["action"] == ACTION_REJECT
        assert "Path exceeds" in obj["reason"]


def test_redirect_map_present(manifest):
    redirect_map = manifest.get("redirect_map", {})
    assert isinstance(redirect_map, dict), "redirect_map must be a dict"

    legacy = [r for r in manifest["objects"] if r["action"] == ACTION_ID_BACKFILL]
    for obj in legacy:
        assert obj["source_path"] in redirect_map, (
            f"Legacy object {obj['source_path']} missing from redirect_map"
        )
        assert redirect_map[obj["source_path"]] == obj["domain_resource_id"], (
            f"redirect_map entry mismatch for {obj['source_path']}"
        )


def test_ledger_tombstone_avoidance(tmp_path):
    try:
        from katana_kernel.ledger import ResourceIdLedger
    except ImportError:
        pytest.skip("katana-kernel not available")

    root = tmp_path / "tombstone_test"
    root.mkdir()
    (root / "card.md").write_text(
        "---\nname: test\ndescription: test desc\n---\n\n## Fact\nContent\n",
        encoding="utf-8",
    )

    ledger_path = str(tmp_path / "tombstones.json")
    ledger = ResourceIdLedger(ledger_path)

    config = [{
        "name": "ts",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "memory_legacy",
        "prefix": "m-",
        "destination_repo": "/test",
        "default_action": "id_backfill",
        "include": ["*.md"],
    }]

    manifest1 = run_inventory(config, migration_run_id="ts-test", ledger_path=ledger_path)
    obj1 = manifest1["objects"][0]
    id1 = obj1["domain_resource_id"]

    ledger.tombstone(id1)
    ledger2 = ResourceIdLedger(ledger_path)
    assert ledger2.is_tombstoned(id1)

    manifest2 = run_inventory(config, migration_run_id="ts-test", ledger_path=ledger_path)
    obj2 = manifest2["objects"][0]
    id2 = obj2["domain_resource_id"]

    assert id1 != id2, f"Tombstoned ID {id1} was reused ({id2})"
    assert id2.startswith("m-")
    assert len(id2) == 8


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
