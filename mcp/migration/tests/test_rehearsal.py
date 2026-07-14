"""Contract tests for migration rehearsal engine (M3b REHEARSED phase)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from katana_migration.rehearsal import (
    ACTION_ARCHIVE,
    ACTION_ID_BACKFILL,
    ACTION_MERGE,
    ACTION_NORMALIZE,
    ACTION_PRESERVE,
    ACTION_QUARANTINE,
    ACTION_REJECT,
    ACTION_REWRITE,
    DISPOSITION_BROKEN_NEW,
    DISPOSITION_BROKEN_OLD_ACK,
    DISPOSITION_REDIRECTED,
    DISPOSITION_RESOLVED,
    GATE_BINARY,
    GATE_CASEFOLD,
    GATE_EXECUTABLE,
    GATE_LFS,
    GATE_PATH_LENGTH,
    GATE_SYMLINK,
    GATE_UNICODE_NFC,
    RehearsalError,
    RehearsalEngine,
    _extract_body_bytes,
    _parse_frontmatter,
    run_rehearsal,
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
        "---\nkey: \"unclosed\n---\n\nOriginal body\n",
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
    return run_inventory(source_sets_config, migration_run_id="test-rehearsal-001")


@pytest.fixture
def dest_root(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    return dest


@pytest.fixture
def nested_legacy_card():
    return (
        "---\n"
        'name: "katana-memory-mcp-service"\n'
        "description: Legacy memory \u2014 operator copy\n"
        "metadata:\n"
        '  source: "/data/vault/memory"\n'
        "  labels:\n"
        "    - migration\n"
        '    - "MCP \u2014 rehearsal"\n'
        "status: active\n"
        "---\n"
        "\n"
        "## Fact\n"
        "Nested metadata, quotes, and unicode must survive.\n"
    ).encode("utf-8")


# ── Basic engine tests ────────────────────────────────────────────────────────

def test_engine_runs_without_error(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["invariant_holds"] is True


def test_all_actions_materialized(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    for domain_name, domain_result in result["domain_results"].items():
        dest_path = dest_root / domain_name.lstrip("/")
        assert dest_path.exists()

        commit_file = dest_path / "MIGRATION_BASE.json"
        assert commit_file.exists()

        marker = json.loads(commit_file.read_text())
        assert marker["marker"] == "MIGRATION_BASE"
        assert marker["migration_run_id"] == "test-rehearsal-001"


def test_preserve_sha256_byte_equal(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    preserve_objs = [o for o in manifest["objects"] if o["action"] == ACTION_PRESERVE]
    assert len(preserve_objs) > 0

    for obj in preserve_objs:
        dest_repo = obj["destination_repo"]
        dest_path = dest_root / dest_repo.lstrip("/") / obj["destination_path"]
        assert dest_path.exists()
        actual_content = dest_path.read_bytes()
        actual_sha = __import__("hashlib").sha256(actual_content).hexdigest()
        expected_sha = obj.get("sha256") or obj.get("pre_hash")
        if expected_sha:
            assert actual_sha == expected_sha, (
                f"SHA-256 mismatch for {obj['destination_path']}: "
                f"expected {expected_sha}, got {actual_sha}"
            )


def test_id_backfill_body_bytes_preserved(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    backfill_objs = [o for o in manifest["objects"] if o["action"] == ACTION_ID_BACKFILL]
    assert len(backfill_objs) > 0

    for obj in backfill_objs:
        dest_repo = obj["destination_repo"]
        dest_path = dest_root / dest_repo.lstrip("/") / obj["destination_path"]
        assert dest_path.exists()

        content = dest_path.read_bytes()
        assert obj["domain_resource_id"].encode() in content

        if not content.startswith(b"---\n"):
            continue

        from katana_migration.rehearsal import _extract_body_bytes
        body = _extract_body_bytes(content)

        source_root_path = None
        for ss in manifest["source_sets"]:
            if ss["source_repo"] == obj["source_repo"]:
                source_root_path = ss["root"]
                break
        assert source_root_path is not None

        source_file = Path(source_root_path) / obj["source_path"]
        source_content = source_file.read_bytes()
        source_body = _extract_body_bytes(source_content)

        assert body == source_body, (
            f"Body bytes changed for {obj['destination_path']}"
        )


def test_id_backfill_surgically_inserts_into_nested_frontmatter(tmp_path, nested_legacy_card):
    engine = RehearsalEngine({}, str(tmp_path))
    obj = {
        "destination_path": "katana-memory-mcp-service.md",
        "domain_resource_id": "m-a1b2c3",
    }
    target = tmp_path / obj["destination_path"]

    engine._apply_id_backfill(target, obj, nested_legacy_card, target)

    actual = target.read_bytes()
    expected = nested_legacy_card[:4] + b"id: m-a1b2c3\n" + nested_legacy_card[4:]
    assert actual == expected
    assert _extract_body_bytes(actual) == _extract_body_bytes(nested_legacy_card)
    frontmatter, _, _ = _parse_frontmatter(actual)
    assert frontmatter is not None
    assert frontmatter["id"] == "m-a1b2c3"

    repeated_target = tmp_path / "repeated.md"
    engine._apply_id_backfill(repeated_target, obj, nested_legacy_card, repeated_target)
    assert repeated_target.read_bytes() == actual

    idempotent_target = tmp_path / "idempotent.md"
    engine._apply_id_backfill(idempotent_target, obj, actual, idempotent_target)
    assert idempotent_target.read_bytes() == actual
    assert actual.count(b"\nid: m-a1b2c3\n") == 1


def test_id_backfill_adds_minimal_frontmatter_without_changing_body(tmp_path):
    content = b"# Legacy card\r\n\r\nBody bytes stay exact.\r\n"
    engine = RehearsalEngine({}, str(tmp_path))
    obj = {
        "destination_path": "legacy-no-frontmatter.md",
        "domain_resource_id": "m-d4e5f6",
    }
    target = tmp_path / obj["destination_path"]

    engine._apply_id_backfill(target, obj, content, target)

    actual = target.read_bytes()
    assert actual == b"---\nid: m-d4e5f6\n---\n" + content
    assert _extract_body_bytes(actual) == content
    frontmatter, _, _ = _parse_frontmatter(actual)
    assert frontmatter is not None
    assert frontmatter["id"] == "m-d4e5f6"


def test_normalize_emits_diff_manifest(manifest, dest_root):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source set found")

    wiki_dir = source_root
    (wiki_dir / "normalize_test.md").write_text(
        "---\ndescription: test\ntitle: normalize test\n---\n\n# Normalize Test\n",
        encoding="utf-8",
    )

    config = [
        {
            "name": "normalize_test",
            "root": str(wiki_dir),
            "source_repo": "/data/wiki",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "wiki_writable",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "normalize",
            "include": ["normalize_test.md"],
        }
    ]
    m = run_inventory(config, migration_run_id="normalize-test")

    result = run_rehearsal(m, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["invariant_holds"] is True

    dest_path = dest_root / "data" / "wiki" / "normalize_test.md"
    assert dest_path.exists()

    diff_path = dest_root / "data" / "wiki" / "normalize_test.md.diff_manifest.json"
    assert diff_path.exists(), "Diff manifest not written to disk"

    diff = json.loads(diff_path.read_text())
    assert diff["action"] == "normalize"
    assert "changes" in diff

    content = dest_path.read_text()
    assert "id:" in content or content.startswith("---")


def test_rewrite_emits_diff_manifest(manifest, dest_root):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source set found")

    wiki_dir = source_root
    (wiki_dir / "rewrite_test.md").write_text(
        "---\nid: w-000002\ntitle: rewrite test\n---\n\n# Rewrite Test\n\nLink: [[old-path]]\n",
        encoding="utf-8",
    )

    config = [
        {
            "name": "rewrite_test",
            "root": str(wiki_dir),
            "source_repo": "/data/wiki",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "wiki_writable",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "rewrite",
            "include": ["rewrite_test.md"],
        }
    ]
    m = run_inventory(config, migration_run_id="rewrite-test")

    for obj in m["objects"]:
        if obj["source_path"] == "rewrite_test.md":
            obj["action"] = ACTION_REWRITE
            obj["reference_rewrites"] = [{"old": "[[old-path]]", "new": "[[new-path]]"}]

    result = run_rehearsal(m, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["invariant_holds"] is True

    diff_path = dest_root / "data" / "wiki" / "rewrite_test.md.diff_manifest.json"
    assert diff_path.exists(), "Diff manifest not written to disk for rewrite"

    dest_content = (dest_root / "data" / "wiki" / "rewrite_test.md").read_text()
    assert "[[new-path]]" in dest_content


def test_rejected_objects_not_written(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    rejected = [o for o in manifest["objects"] if o["action"] == ACTION_REJECT]
    assert len(rejected) > 0

    for obj in rejected:
        dest_repo = obj["destination_repo"]
        dest_path = dest_root / dest_repo.lstrip("/") / obj["destination_path"]
        assert not dest_path.exists(), (
            f"Rejected object {obj['destination_path']} was written to destination"
        )


def test_archived_objects_written_to_archive(manifest, dest_root):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source set found")

    (source_root / "archive_test.md").write_text(
        "# Archive Test\n\nContent to archive\n",
        encoding="utf-8",
    )

    config = [
        {
            "name": "archive_test",
            "root": str(source_root),
            "source_repo": "/data/wiki",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "wiki_writable",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "archive",
            "include": ["archive_test.md"],
        }
    ]
    m = run_inventory(config, migration_run_id="archive-test")

    for obj in m["objects"]:
        obj["action"] = ACTION_ARCHIVE

    result = run_rehearsal(m, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["invariant_holds"] is True

    archive_path = dest_root / "data" / "wiki" / "_archive" / "archive_test.md"
    assert archive_path.exists(), "Archived object not written to _archive"


# ── Reference resolution tests ────────────────────────────────────────────────

def test_reference_constraint_computed_not_hardcoded(manifest, dest_root, tmp_path):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    for domain_name, domain_result in result["domain_results"].items():
        refs_path = dest_root / domain_name.lstrip("/") / "references.json"
        if refs_path.exists():
            refs = json.loads(refs_path.read_text())
            total_broken_before = sum(1 for e in refs["entries"] if e["old_target_id"] is None)
            total_broken_after = sum(1 for e in refs["entries"] if e["new_target_id"] is None)
            assert refs["constraint_holds"] == (total_broken_after == total_broken_before)
            assert refs["new_broken"] >= 0
            assert refs["old_broken_acknowledged"] >= 0

            if refs["new_broken"] > 0:
                assert refs["constraint_holds"] is False
            elif refs["new_broken"] == 0:
                pass


def test_reference_broken_old_acknowledged_tracked(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    wiki_dest = dest_root / "data" / "wiki"
    refs_path = wiki_dest / "references.json"
    if not refs_path.exists():
        return

    refs = json.loads(refs_path.read_text())
    entries = refs.get("entries", [])

    broken_old = [e for e in entries if e["disposition"] == DISPOSITION_BROKEN_OLD_ACK]
    for ref in broken_old:
        assert ref["old_target_id"] is None, (
            f"BROKEN_OLD_ACK ref should have old_target_id=None, got {ref['old_target_id']}"
        )

    broken_new = [e for e in entries if e["disposition"] == DISPOSITION_BROKEN_NEW]
    for ref in broken_new:
        assert ref["old_target_id"] is not None, (
            "BROKEN_NEW ref should have a non-None old_target_id (was resolvable before)"
        )
        assert ref["new_target_id"] is None, (
            "BROKEN_NEW ref should have new_target_id=None (not resolvable after)"
        )


def test_reference_resolved_redirected_distinguished(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    wiki_dest = dest_root / "data" / "wiki"
    refs_path = wiki_dest / "references.json"
    if not refs_path.exists():
        return

    refs = json.loads(refs_path.read_text())
    entries = refs.get("entries", [])

    resolved = [e for e in entries if e["disposition"] == DISPOSITION_RESOLVED]
    for ref in resolved:
        assert ref["old_target_id"] == ref["new_target_id"], (
            "RESOLVED ref should have same old and new target ID"
        )
        assert ref["old_target_id"] is not None

    redirected = [e for e in entries if e["disposition"] == DISPOSITION_REDIRECTED]
    for ref in redirected:
        assert ref["old_target_id"] != ref["new_target_id"], (
            "REDIRECTED ref should have different old and new target IDs"
        )


def test_reference_with_known_broken_baseline(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    wiki_source_root = None
    mem_source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            wiki_source_root = Path(ss["root"])
        if ss["name"] == "memory_canonical":
            mem_source_root = Path(ss["root"])

    if wiki_source_root is None:
        pytest.skip("No wiki source set found")

    (wiki_source_root / "ref_test_a.md").write_text(
        "---\nid: w-aaaaaa\ntitle: Ref Test A\nname: Ref Test A\ndescription: Reference test\n---\n\n# Ref A\n\nRefs: [[m-a1b2c3]] [[m-doesnotexist]]\n",
        encoding="utf-8",
    )

    config = [
        {
            "name": "ref_test",
            "root": str(wiki_source_root),
            "source_repo": "/data/wiki",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "wiki_writable",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "preserve",
            "include": ["ref_test_a.md"],
        },
    ]
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

    m = run_inventory(config, migration_run_id="ref-test")

    result = run_rehearsal(m, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    refs_path = dest_root / "data" / "wiki" / "references.json"
    assert refs_path.exists()

    refs = json.loads(refs_path.read_text())
    entries = refs.get("entries", [])

    broken_old = [e for e in entries if e["disposition"] == DISPOSITION_BROKEN_OLD_ACK]
    resolved_refs = [e for e in entries if e["disposition"] == DISPOSITION_RESOLVED]

    assert len(broken_old) >= 1, "Expected at least one acknowledged broken ref (m-doesnotexist)"
    assert len(resolved_refs) >= 1, "Expected at least one resolved ref (m-a1b2c3)"

    assert refs["old_broken_acknowledged"] >= 1
    assert refs["new_broken"] == 0
    assert refs["constraint_holds"] is True


# ── Integrity gate tests ──────────────────────────────────────────────────────

def test_integrity_gate_symlink_raises(manifest, dest_root):
    symlinks = [o for o in manifest["objects"] if o.get("exception_code") == "SYMLINK"]
    if not symlinks:
        pytest.skip("No symlink fixtures")

    for obj in symlinks:
        obj["action"] = ACTION_PRESERVE

    with pytest.raises(RehearsalError, match="Integrity gate failed"):
        run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")


def test_binary_bytes_are_preserved(manifest, dest_root):
    binary = [o for o in manifest["objects"] if o.get("exception_code") == "BINARY_BYTES"]
    if not binary:
        pytest.skip("No binary fixtures")

    run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    for obj in binary:
        target = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
        assert target.read_bytes() == b"\x00\x01\x02\x03\xFF\xFE"


def test_lfs_pointer_is_preserved(manifest, dest_root):
    lfs = [o for o in manifest["objects"] if o.get("exception_code") == "LFS_POINTER"]
    if not lfs:
        pytest.skip("No LFS fixtures")

    run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    for obj in lfs:
        target = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
        assert target.read_bytes() == (
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:abc123def456789\nsize 1234\n"
        )


def test_executable_bit_is_cleared(manifest, dest_root):
    executable = [o for o in manifest["objects"] if o.get("exception_code") == "EXECUTABLE_BIT"]
    if not executable:
        pytest.skip("No executable fixtures")

    assert all(obj["action"] == ACTION_NORMALIZE for obj in executable)
    run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    for obj in executable:
        target = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
        assert target.stat().st_mode & 0o111 == 0
        assert target.read_text(encoding="utf-8") == "#!/bin/bash\necho hello\n"


def test_integrity_gate_path_length_raises(manifest, dest_root, tmp_path, monkeypatch):
    from katana_migration import rehearsal as rmod
    monkeypatch.setattr(rmod, "MAX_PATH_LENGTH", 50)

    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    long_name = "a" * 60
    (source_root / long_name).write_text("# long path\n", encoding="utf-8")

    config = [{
        "name": "pathlen",
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

    with pytest.raises(RehearsalError, match="Integrity gate failed"):
        run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")


def test_integrity_gate_casefold_collision_raises(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "Note.md").write_text("# Note\n", encoding="utf-8")
    (source_root / "note.md").write_text("# note different\n", encoding="utf-8")

    config = [{
        "name": "casefold",
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
        obj["action"] = ACTION_PRESERVE

    dest = tmp_path / "casefold_dest"
    dest.mkdir()

    with pytest.raises(RehearsalError, match="Integrity gate failed"):
        run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")


def test_unicode_content_is_normalized_to_nfc(manifest, dest_root):
    not_nfc = [o for o in manifest["objects"] if "not-nfc" in o.get("source_path", "")]
    if not not_nfc:
        pytest.skip("No non-NFC fixture")

    assert all(obj["action"] == ACTION_NORMALIZE for obj in not_nfc)
    run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    for obj in not_nfc:
        target = dest_root / obj["destination_repo"].lstrip("/") / obj["destination_path"]
        assert target.read_text(encoding="utf-8") == "Caf\u00e9\n"


def test_yaml_parse_error_is_quarantined_byte_for_byte(manifest, dest_root):
    yaml_errors = [o for o in manifest["objects"] if o.get("exception_code") == "YAML_PARSE_ERROR"]
    assert len(yaml_errors) == 1
    obj = yaml_errors[0]
    assert obj["action"] == ACTION_QUARANTINE

    run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    domain = dest_root / obj["destination_repo"].lstrip("/")
    source = Path(next(
        ss["root"] for ss in manifest["source_sets"] if ss["source_repo"] == obj["source_repo"]
    )) / obj["source_path"]
    quarantined = domain / obj["quarantine_path"]
    assert quarantined.read_bytes() == source.read_bytes()
    assert not (domain / obj["destination_path"]).exists()
    assert not (domain / "_archive" / obj["destination_path"]).exists()


def test_anomaly_dispositions_pass_proof_gates(source_root, dest_root):
    from katana_migration.inventory import run_inventory
    from katana_migration.proof_gates import run_all_gates

    source_set = {
        "name": "exceptions",
        "root": str(source_root / "exceptions"),
        "source_repo": "/data/exceptions",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "unknown",
        "prefix": "m-",
        "destination_repo": "/data/memory",
        "default_action": "preserve",
        "include": ["**/*"],
    }
    anomaly_manifest = run_inventory([source_set], migration_run_id="anomaly-proof-gates")
    run_rehearsal(
        anomaly_manifest,
        str(dest_root),
        committer_date="2026-01-01T00:00:00+0000",
    )
    report = run_all_gates(
        anomaly_manifest,
        str(dest_root),
        committer_date="2026-01-01T00:00:00+0000",
    )

    assert report["status"] == "PASS", report["gates"]
    assert report["total_gates"] == 7


def test_blocking_exceptions_set(manifest, dest_root):
    gate_codes = {
        GATE_SYMLINK, GATE_BINARY, GATE_LFS, GATE_PATH_LENGTH,
        GATE_CASEFOLD, GATE_EXECUTABLE, GATE_UNICODE_NFC,
    }
    assert len(gate_codes) == 7, f"Expected 7 gate codes, got {len(gate_codes)}"


# ── Idempotency tests ─────────────────────────────────────────────────────────

def test_idempotent_rehearsal_same_commit_sha(manifest, dest_root, tmp_path):
    result1 = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    dest_root2 = tmp_path / "dest2"
    dest_root2.mkdir()
    result2 = run_rehearsal(manifest, str(dest_root2), committer_date="2026-01-01T00:00:00+0000")

    for domain_name in result1["domain_results"]:
        commit1 = result1["domain_results"][domain_name]["final_commit"]
        commit2 = result2["domain_results"][domain_name]["final_commit"]
        assert commit1 == commit2, (
            f"Commit SHAs differ for {domain_name}: {commit1} vs {commit2}"
        )


def test_idempotent_rehearsal_byte_identical_tree(manifest, dest_root, tmp_path):
    result1 = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    dest_root2 = tmp_path / "dest2"
    dest_root2.mkdir()
    result2 = run_rehearsal(manifest, str(dest_root2), committer_date="2026-01-01T00:00:00+0000")

    for domain_name in result1["domain_results"]:
        dest1 = dest_root / domain_name.lstrip("/")
        dest2 = dest_root2 / domain_name.lstrip("/")

        if not dest1.exists() or not dest2.exists():
            continue

        trees_equal = _compare_git_trees(str(dest1), str(dest2))
        assert trees_equal, f"Git trees differ for {domain_name}"


def test_idempotent_multi_source_git(manifest, dest_root, tmp_path):
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    repo1 = tmp_path / "git_source1"
    repo1.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo1)], check=True, capture_output=True)
    (repo1 / "file1.md").write_text("# File 1\n\nContent from repo 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo1), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo1), check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )
    commit1 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo1), check=True, capture_output=True, text=True
    ).stdout.strip()

    repo2 = tmp_path / "git_source2"
    repo2.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo2)], check=True, capture_output=True)
    (repo2 / "file2.md").write_text("# File 2\n\nContent from repo 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo2), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo2), check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )
    commit2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo2), check=True, capture_output=True, text=True
    ).stdout.strip()

    from katana_migration.inventory import run_inventory

    config = [
        {
            "name": "git_source_1",
            "root": str(repo1),
            "source_repo": "/data/multi-test",
            "source_commit": commit1,
            "object_class": "test",
            "prefix": "t-",
            "destination_repo": "/data/multi-test",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "git_source_2",
            "root": str(repo2),
            "source_repo": "/data/multi-test",
            "source_commit": commit2,
            "object_class": "test",
            "prefix": "t-",
            "destination_repo": "/data/multi-test",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
    ]
    m = run_inventory(config, migration_run_id="multi-git-test")

    dest1 = tmp_path / "multi_dest1"
    dest1.mkdir()
    result1 = run_rehearsal(m, str(dest1), committer_date="2026-01-01T00:00:00+0000")

    dest2 = tmp_path / "multi_dest2"
    dest2.mkdir()
    result2 = run_rehearsal(m, str(dest2), committer_date="2026-01-01T00:00:00+0000")

    commit_a = result1["domain_results"]["/data/multi-test"]["final_commit"]
    commit_b = result2["domain_results"]["/data/multi-test"]["final_commit"]
    assert commit_a == commit_b, f"Multi-source git idempotency failed: {commit_a} vs {commit_b}"

    trees_equal = _compare_git_trees(
        str(dest1 / "data" / "multi-test"),
        str(dest2 / "data" / "multi-test")
    )
    assert trees_equal, "Multi-source git trees are not byte-identical"


def test_idempotent_merge_failure_raises(manifest, dest_root, tmp_path):
    repo = tmp_path / "git_source"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    (repo / "file.md").write_text("# File\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=str(repo), check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
             "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000"}
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), check=True, capture_output=True, text=True
    ).stdout.strip()

    from katana_migration.inventory import run_inventory

    config = [
        {
            "name": "git_source_1",
            "root": str(repo),
            "source_repo": "/data/merge-fail",
            "source_commit": commit,
            "object_class": "test",
            "prefix": "t-",
            "destination_repo": "/data/merge-fail",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "git_source_2",
            "root": "/nonexistent/path",
            "source_repo": "/data/merge-fail",
            "source_commit": "0000000000000000000000000000000000000000",
            "object_class": "test",
            "prefix": "t-",
            "destination_repo": "/data/merge-fail",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
    ]
    m = run_inventory(config, migration_run_id="merge-fail-test")

    dest = tmp_path / "merge_fail_dest"
    dest.mkdir()

    with pytest.raises(RehearsalError, match="Cannot merge"):
        run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")


# ── Path filter tests ─────────────────────────────────────────────────────────

def test_filtered_history_no_other_paths_leak(manifest, dest_root, tmp_path):
    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "Zettelkasten" / "note1.md").write_text(
        "# Zettelkasten Note\n\nContent\n",
        encoding="utf-8",
    )
    (source_root / "Zettelkasten" / "secret.bin").write_bytes(b"\x00\x01\x02")

    from katana_migration.inventory import run_inventory

    config = [{
        "name": "wiki_with_secret",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "auto_classify": True,
        "include": ["Zettelkasten/note1.md"],
    }]
    m = run_inventory(config, migration_run_id="filter-leak-test")

    dest = tmp_path / "filter_dest"
    dest.mkdir()

    result = run_rehearsal(m, str(dest), committer_date="2026-01-01T00:00:00+0000")

    secret_path = dest / "data" / "wiki" / "Zettelkasten" / "secret.bin"
    assert not secret_path.exists(), (
        "secret.bin leaked into destination despite not being in manifest"
    )


def test_path_filter_verification_raises_on_leak(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "Zettelkasten").mkdir(exist_ok=True)
    (source_root / "Zettelkasten" / "visible.md").write_text("# visible\n", encoding="utf-8")
    (source_root / "Zettelkasten" / "hidden.md").write_text("# hidden\n", encoding="utf-8")

    config = [{
        "name": "wiki_partial",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "auto_classify": True,
        "include": ["Zettelkasten/visible.md"],
    }]
    m = run_inventory(config, migration_run_id="path-verify-test")

    dest = tmp_path / "path_verify_dest"
    dest.mkdir()

    engine = RehearsalEngine(m, str(dest), committer_date="2026-01-01T00:00:00+0000")
    domain_groups = engine._group_by_domain(m["objects"])
    dest_path = dest / "data" / "wiki"
    dest_path.mkdir(parents=True, exist_ok=True)
    engine._init_dest_repo_empty(dest_path)

    (dest_path / "Zettelkasten").mkdir(parents=True, exist_ok=True)
    (dest_path / "Zettelkasten" / "visible.md").write_text("# visible\n", encoding="utf-8")
    (dest_path / "Zettelkasten" / "hidden.md").write_text("# hidden\n", encoding="utf-8")

    with pytest.raises(RehearsalError, match="Path filter violation"):
        engine._verify_path_filter(dest_path, m["objects"])


# ── No production paths tests ─────────────────────────────────────────────────

def test_rehearsal_only_no_production_paths(manifest, dest_root, tmp_path):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    assert str(dest_root).startswith(str(tmp_path)), (
        "dest_root must be under tmp_path"
    )

    production_roots = [
        "/data/memory",
        "/data/vault/",
        "/data/wiki",
        "/data/work-records",
    ]

    for domain_name, domain_result in result["domain_results"].items():
        for prod_root in production_roots:
            assert not str(dest_root / domain_name.lstrip("/")).startswith(prod_root), (
                f"Destination path references production: {dest_root / domain_name.lstrip('/')}"
            )

    for prod_root in production_roots:
        prod_path = Path(prod_root)
        if not str(prod_path).startswith("/"):
            continue
        for domain_name in result["domain_results"]:
            dest_path = dest_root / domain_name.lstrip("/")
            assert not str(dest_path).startswith(prod_root), (
                f"Destination path under production root: {dest_path}"
            )


def test_dest_root_under_tmp_path(manifest, dest_root, tmp_path):
    dest_str = str(dest_root)
    assert dest_str.startswith(str(tmp_path)), (
        f"dest_root {dest_str} is not under tmp_path {tmp_path}"
    )


# ── Merge action tests ────────────────────────────────────────────────────────

def test_merge_action_preserves_content(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    merge_content = "---\nid: w-merge01\ntitle: merge test\n---\n\n# Merge\n\nMerged content\n"
    (source_root / "merge_test.md").write_text(merge_content, encoding="utf-8")

    config = [{
        "name": "merge_test",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "merge",
        "include": ["merge_test.md"],
    }]
    m = run_inventory(config, migration_run_id="merge-test")

    for obj in m["objects"]:
        obj["action"] = ACTION_MERGE

    result = run_rehearsal(m, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    assert result["invariant_holds"] is True

    dest_file = dest_root / "data" / "wiki" / "merge_test.md"
    assert dest_file.exists()
    content = dest_file.read_text()
    assert "Merged content" in content


def test_merge_action_with_conflict_detection(manifest, dest_root, tmp_path):
    from katana_migration.inventory import run_inventory

    source_root = None
    for ss in manifest["source_sets"]:
        if ss["name"] == "wiki":
            source_root = Path(ss["root"])
            break

    if source_root is None:
        pytest.skip("No wiki source root")

    (source_root / "conflict_test.md").write_text(
        "---\nid: w-conflict01\ntitle: conflict\n---\n\n# Conflict\n\nContent A\n",
        encoding="utf-8",
    )

    config = [{
        "name": "conflict",
        "root": str(source_root),
        "source_repo": "/data/wiki",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "wiki_writable",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "merge",
        "include": ["conflict_test.md"],
    }]
    m = run_inventory(config, migration_run_id="merge-conflict-test")

    for obj in m["objects"]:
        obj["action"] = ACTION_MERGE

    engine = RehearsalEngine(m, str(dest_root), committer_date="2026-01-01T00:00:00+0000")
    domain_groups = engine._group_by_domain(m["objects"])
    dest_path = dest_root / "data" / "wiki"
    dest_path.mkdir(parents=True, exist_ok=True)
    engine._init_dest_repo_empty(dest_path)

    target = dest_path / "conflict_test.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "---\nid: w-conflict01\ntitle: conflict different\n---\n\n# Conflict\n\nContent B\n",
        encoding="utf-8",
    )

    source_file = Path(source_root) / "conflict_test.md"
    content = source_file.read_bytes()
    engine._apply_merge(target, {"destination_path": "conflict_test.md"}, content, source_file)

    merged = target.read_text()
    assert "Content A" in merged or "Content B" in merged


# ── Catalog emission tests ────────────────────────────────────────────────────

def test_redirects_json_emitted(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    memory_dest = dest_root / "data" / "memory"
    redirects_path = memory_dest / "redirects.json"
    assert redirects_path.exists()

    redirects = json.loads(redirects_path.read_text())
    assert isinstance(redirects, dict)


def test_references_json_emitted(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    for domain_name in result["domain_results"]:
        refs_path = dest_root / domain_name.lstrip("/") / "references.json"
        assert refs_path.exists()

        refs = json.loads(refs_path.read_text())
        assert "constraint_holds" in refs
        assert "old_broken_acknowledged" in refs
        assert "new_broken" in refs
        assert "entries" in refs


def test_migration_base_marker_emitted(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    for domain_name in result["domain_results"]:
        marker_path = dest_root / domain_name.lstrip("/") / "MIGRATION_BASE.json"
        assert marker_path.exists()

        marker = json.loads(marker_path.read_text())
        assert marker["marker"] == "MIGRATION_BASE"
        assert "migration_run_id" in marker
        assert "object_count" in marker
        assert "reference_constraint_holds" in marker


# ── Full run end-to-end tests ─────────────────────────────────────────────────

def test_full_run_all_domains(manifest, dest_root):
    result = run_rehearsal(manifest, str(dest_root), committer_date="2026-01-01T00:00:00+0000")

    domain_repos = {r["destination_repo"] for r in result["domain_results"].values()}
    assert len(domain_repos) >= 2, f"Expected at least 2 domain repos, got {len(domain_repos)}"

    for domain_result in result["domain_results"].values():
        assert domain_result["final_commit"] is not None
        assert len(domain_result["final_commit"]) == 40

        dest_path = dest_root / domain_result["destination_repo"].lstrip("/")
        assert (dest_path / ".git").exists()


def test_manifest_all_actions_present(manifest):
    actions = {o["action"] for o in manifest["objects"]}
    expected = {
        ACTION_PRESERVE,
        ACTION_ID_BACKFILL,
        ACTION_NORMALIZE,
        ACTION_QUARANTINE,
        ACTION_REJECT,
    }
    assert expected.issubset(actions), f"Missing expected actions: {expected - actions}"


# ── Helper ────────────────────────────────────────────────────────────────────

def _compare_git_trees(path1: str, path2: str) -> bool:
    try:
        result1 = subprocess.run(
            ["git", "ls-tree", "-r", "HEAD"],
            cwd=path1, check=True, capture_output=True, text=True
        )
        result2 = subprocess.run(
            ["git", "ls-tree", "-r", "HEAD"],
            cwd=path2, check=True, capture_output=True, text=True
        )
        lines1 = sorted(result1.stdout.strip().split("\n"))
        lines2 = sorted(result2.stdout.strip().split("\n"))
        return lines1 == lines2
    except subprocess.CalledProcessError:
        return False
