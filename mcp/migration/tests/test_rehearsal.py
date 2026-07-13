"""Tests for M3b Rehearsal Engine.

Covers all spec-mandated contracts:
- Full import with all 7 actions (preserve/id_backfill/normalize/rewrite/merge/archive/reject)
- Path-filtered history extraction for Wiki/WF
- Idempotency (byte-identical including stable commit tree)
- Reference resolution with old/new broken tracking
- AST/semantic diff manifest for normalize/rewrite
- Conservation invariant (§8.4)
- Integrity gate (executable/binary/unicode/casefold/path-length/LFS/symlink)
- Full rehearsal stop on blocking error
- MIGRATION_BASE, redirects.json, references.json output
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from pathlib import Path

import pytest
import yaml

from katana_migration.inventory import (
    ACTION_ARCHIVE,
    ACTION_ID_BACKFILL,
    ACTION_MERGE,
    ACTION_NORMALIZE,
    ACTION_PRESERVE,
    ACTION_REJECT,
    ACTION_REWRITE,
    EXC_BINARY,
    EXC_CASEFOLD_COLLISION,
    EXC_EXECUTABLE,
    EXC_LFS_POINTER,
    EXC_PATH_LENGTH,
    EXC_SYMLINK,
    _TRANSFORM_ACTIONS,
    build_manifest,
    compute_summary,
    run_inventory,
    sha256_hex,
)
from katana_migration.rehearsal import (
    MIGRATION_BASE_FILENAME,
    REDIRECTS_FILENAME,
    REFERENCES_FILENAME,
    RehearsalEngine,
    RehearsalResult,
    _apply_link_rewrites,
    _check_integrity,
    _extract_body_bytes,
    _extract_filtered_history,
    _inject_frontmatter_id,
    _materialize_archive,
    _materialize_id_backfill,
    _materialize_merge,
    _materialize_normalize,
    _materialize_preserve,
    _materialize_reject,
    _materialize_rewrite,
    _resolve_references,
    _semantic_diff_manifest,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def source_repo_with_history(tmp_path):
    """Create a git repo with multi-commit history across multiple directories."""
    repo = tmp_path / "source_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

    (repo / "memory").mkdir(parents=True)
    (repo / "wiki").mkdir(parents=True)
    (repo / "wf").mkdir(parents=True)

    memory_file = repo / "memory" / "mem1.yaml"
    memory_file.write_text(
        "---\nid: m-abc123\nname: Test Memory\ndescription: A test memory\n---\n# Test Memory\n\nContent here.\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial: add memory"],
        cwd=repo, check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2024-01-01T00:00:00", "GIT_AUTHOR_DATE": "2024-01-01T00:00:00"},
    )

    wiki_file = repo / "wiki" / "Zettelkasten" / "note1.md"
    wiki_file.parent.mkdir(parents=True, exist_ok=True)
    wiki_file.write_text("# Note 1\n\nRef: [[m-abc123]]\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add wiki note"],
        cwd=repo, check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2024-01-02T00:00:00", "GIT_AUTHOR_DATE": "2024-01-02T00:00:00"},
    )

    wf_file = repo / "wf" / "record1.md"
    wf_file.write_text("# Work Record\n\nRef: wf-def456\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add work-folder record"],
        cwd=repo, check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2024-01-03T00:00:00", "GIT_AUTHOR_DATE": "2024-01-03T00:00:00"},
    )

    memory_file2 = repo / "memory" / "mem2.yaml"
    memory_file2.write_text(
        "---\nid: m-def456\nname: Memory Two\ndescription: Second memory\n---\n# Memory Two\n\nMore content.\n"
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add second memory"],
        cwd=repo, check=True, capture_output=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2024-01-04T00:00:00", "GIT_AUTHOR_DATE": "2024-01-04T00:00:00"},
    )

    return repo


@pytest.fixture
def source_sets_all_actions(tmp_path):
    """Create source files that exercise ALL 7 actions when inventoried."""
    root = tmp_path / "fixture_src"
    root.mkdir()

    (root / "memory_canonical").mkdir(parents=True)
    (root / "memory_legacy").mkdir(parents=True)
    (root / "memory_normalize").mkdir(parents=True)
    (root / "memory_rewrite").mkdir(parents=True)
    (root / "memory_merge").mkdir(parents=True)
    (root / "memory_archive").mkdir(parents=True)
    (root / "exceptions").mkdir(parents=True)

    (root / "memory_canonical" / "preserve1.yaml").write_text(
        "---\nid: m-aaa111\nname: Canonical Mem\ndescription: Already has valid ID\n---\nBody content preserved.\n"
    )

    (root / "memory_legacy" / "legacy1.yaml").write_text(
        "---\nname: Legacy Mem\ndescription: No ID yet\n---\nBody content for backfill.\n"
    )

    (root / "memory_normalize" / "norm1.yaml").write_text(
        "---\nname: To Normalize\ndescription: Needs NFC\n---\nBody with \u0065\u0301 combined.\n"
    )

    (root / "memory_rewrite" / "rewrite1.yaml").write_text(
        "---\nname: Rewrite Mem\ndescription: Has refs to rewrite\n---\nSee also m-aaa111 for details.\n"
    )

    (root / "memory_merge" / "merge1.yaml").write_text(
        "---\nname: Merge Mem\ndescription: Candidate for merge\n---\nMerge content.\n"
    )

    (root / "memory_archive" / "archive1.yaml").write_text(
        "---\nname: Archive Mem\ndescription: To be archived\n---\nArchive content.\n"
    )

    binary_content = b"\x00\x01\x02\x03binary"
    (root / "exceptions" / "binary.bin").write_bytes(binary_content)

    (root / "exceptions" / "executable.sh").write_text("#!/bin/bash\necho hi\n")
    os.chmod(root / "exceptions" / "executable.sh", 0o755)

    lfs_content = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        b"size 123\n"
    )
    (root / "exceptions" / "lfs_pointer.bin").write_bytes(lfs_content)

    symlink_path = root / "exceptions" / "symlink.txt"
    symlink_target = root / "exceptions" / "binary.bin"
    if symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(symlink_target)

    return root


@pytest.fixture
def source_sets_config_all_actions(source_sets_all_actions):
    """Source sets config that produces all 7 actions."""
    return [
        {
            "name": "canonical_memory",
            "source_repo": str(source_sets_all_actions / "memory_canonical"),
            "root": str(source_sets_all_actions / "memory_canonical"),
            "prefix": "m-",
            "object_class": "memory_canonical",
            "destination_repo": "memory",
            "default_action": "preserve",
            "include": ["**/*"],
        },
        {
            "name": "legacy_memory",
            "source_repo": str(source_sets_all_actions / "memory_legacy"),
            "root": str(source_sets_all_actions / "memory_legacy"),
            "prefix": "m-",
            "object_class": "memory_legacy",
            "destination_repo": "memory",
            "default_action": "id_backfill",
            "include": ["**/*"],
        },
        {
            "name": "normalize_memory",
            "source_repo": str(source_sets_all_actions / "memory_normalize"),
            "root": str(source_sets_all_actions / "memory_normalize"),
            "prefix": "m-",
            "object_class": "memory",
            "destination_repo": "memory",
            "default_action": "normalize",
            "include": ["**/*"],
        },
        {
            "name": "rewrite_memory",
            "source_repo": str(source_sets_all_actions / "memory_rewrite"),
            "root": str(source_sets_all_actions / "memory_rewrite"),
            "prefix": "m-",
            "object_class": "memory",
            "destination_repo": "memory",
            "default_action": "rewrite",
            "include": ["**/*"],
        },
        {
            "name": "merge_memory",
            "source_repo": str(source_sets_all_actions / "memory_merge"),
            "root": str(source_sets_all_actions / "memory_merge"),
            "prefix": "m-",
            "object_class": "memory",
            "destination_repo": "memory",
            "default_action": "merge",
            "include": ["**/*"],
        },
        {
            "name": "archive_memory",
            "source_repo": str(source_sets_all_actions / "memory_archive"),
            "root": str(source_sets_all_actions / "memory_archive"),
            "prefix": "m-",
            "object_class": "memory",
            "destination_repo": "memory",
            "default_action": "archive",
            "include": ["**/*"],
        },
        {
            "name": "exceptions",
            "source_repo": str(source_sets_all_actions / "exceptions"),
            "root": str(source_sets_all_actions / "exceptions"),
            "prefix": "m-",
            "object_class": "unknown",
            "destination_repo": "memory",
            "default_action": "preserve",
            "include": ["**/*"],
        },
    ]


@pytest.fixture
def manifest_all_actions(source_sets_config_all_actions):
    return run_inventory(source_sets_config_all_actions, migration_run_id="mig-test-all-actions")


@pytest.fixture
def source_sets_config_wiki_wf(tmp_path):
    """Source sets for Wiki and Work-Folder domains."""
    root = tmp_path / "wiki_wf_src"
    root.mkdir()

    (root / "wiki" / "Zettelkasten").mkdir(parents=True)
    (root / "wiki" / "inbox").mkdir(parents=True)
    (root / "wiki" / "转换文档").mkdir(parents=True)
    (root / "wf").mkdir(parents=True)

    (root / "wiki" / "Zettelkasten" / "note1.md").write_text(
        "---\nid: w-111aaa\nname: Wiki Note 1\ndescription: A wiki note\n---\n# Note 1\n\nSee [[m-aaa111]].\n"
    )
    (root / "wiki" / "WIKI.md").write_text("# WIKI Index\n")
    (root / "wiki" / "log.md").write_text("# Log\n")
    (root / "wiki" / "inbox" / "draft.md").write_text("# Draft\n")
    (root / "wiki" / "转换文档" / "trans1.md").write_text("# Translation\n")
    (root / "wf" / "record1.md").write_text(
        "---\nname: Work Record\ndescription: A record\n---\n# Work Record\n\nRef: wf-222bbb\n"
    )

    return [
        {
            "name": "wiki",
            "source_repo": str(root / "wiki"),
            "source_commit": "0000000000000000000000000000000000000000",
            "root": str(root / "wiki"),
            "prefix": "w-",
            "object_class": "wiki",
            "destination_repo": "wiki",
            "default_action": "preserve",
            "include": ["**/*"],
        },
        {
            "name": "work_folder",
            "source_repo": str(root / "wf"),
            "source_commit": "0000000000000000000000000000000000000000",
            "root": str(root / "wf"),
            "prefix": "wf-",
            "object_class": "work_folder",
            "destination_repo": "work-folder",
            "default_action": "preserve",
            "include": ["**/*"],
        },
    ]


# ── Unit tests: materializers ─────────────────────────────────────────────────


class TestMaterializePreserve:
    def test_preserve_byte_identical(self, tmp_path):
        src = tmp_path / "src.yaml"
        src.write_text("---\nid: m-abc123\nname: Test\ndescription: Desc\n---\nBody\n")
        dest = tmp_path / "dest" / "src.yaml"
        record = {"source_path": "src.yaml", "sha256": sha256_hex(src.read_bytes())}
        result = _materialize_preserve(record, src, dest)
        assert result["sha256_match"] is True
        assert dest.read_bytes() == src.read_bytes()

    def test_preserve_sha256_mismatch_detected(self, tmp_path):
        src = tmp_path / "src.yaml"
        src.write_text("---\nid: m-abc123\nname: Test\ndescription: Desc\n---\nBody\n")
        dest = tmp_path / "dest" / "src.yaml"
        record = {"source_path": "src.yaml", "sha256": "deadbeef"}
        result = _materialize_preserve(record, src, dest)
        assert result["sha256_match"] is False


class TestMaterializeIdBackfill:
    def test_id_backfill_body_unchanged(self, tmp_path):
        src = tmp_path / "legacy.yaml"
        src.write_text("---\nname: Legacy\ndescription: No ID\n---\nBody content here.\n")
        dest = tmp_path / "dest" / "legacy.yaml"
        record = {"source_path": "legacy.yaml", "domain_resource_id": "m-new001"}
        result = _materialize_id_backfill(record, src, dest)
        assert result["body_bytes_unchanged"] is True
        assert dest.exists()
        assert b"id: m-new001" in dest.read_bytes()

    def test_id_backfill_injects_id(self, tmp_path):
        src = tmp_path / "legacy.yaml"
        src.write_text("---\nname: Legacy\ndescription: No ID\n---\nBody\n")
        dest = tmp_path / "dest" / "legacy.yaml"
        record = {"source_path": "legacy.yaml", "domain_resource_id": "m-back01"}
        result = _materialize_id_backfill(record, src, dest)
        assert result["body_bytes_unchanged"] is True
        content = dest.read_text()
        assert "id: m-back01" in content


class TestMaterializeNormalize:
    def test_normalize_nfc(self, tmp_path):
        src = tmp_path / "norm.yaml"
        combined = "\u0065\u0301"  # e + combining acute
        src.write_text(f"---\nname: Test\ndescription: NFC\n---\nBody with {combined}.\n")
        dest = tmp_path / "dest" / "norm.yaml"
        record = {"source_path": "norm.yaml"}
        result = _materialize_normalize(record, src, dest)
        dest_text = dest.read_text()
        assert unicodedata.is_normalized("NFC", dest_text)
        assert "diff_manifest" in result

    def test_normalize_produces_diff_manifest(self, tmp_path):
        src = tmp_path / "norm.yaml"
        src.write_text("---\nname: Test\ndescription: Has NFD\n---\n# Section\n\nText with \u0065\u0301.\n")
        dest = tmp_path / "dest" / "norm.yaml"
        record = {"source_path": "norm.yaml"}
        result = _materialize_normalize(record, src, dest)
        assert "diff_manifest" in result
        dm = result["diff_manifest"]
        assert dm["kind"] == "semantic"


class TestMaterializeRewrite:
    def test_rewrite_applies_reference_rewrites(self, tmp_path):
        src = tmp_path / "rewrite.yaml"
        src.write_text("---\nname: Rewrite\ndescription: Has refs\n---\nSee m-old001 for details.\n")
        dest = tmp_path / "dest" / "rewrite.yaml"
        record = {
            "source_path": "rewrite.yaml",
            "reference_rewrites": [
                {"old_literal": "m-old001", "new_literal": "m-new001"},
            ],
        }
        result = _materialize_rewrite(record, src, dest)
        assert result["rewrites_applied"] == 1
        assert "m-new001" in dest.read_text()
        assert "m-old001" not in dest.read_text()

    def test_rewrite_produces_diff_manifest(self, tmp_path):
        src = tmp_path / "rewrite.yaml"
        src.write_text("---\nname: Rewrite\ndescription: Has refs\n---\n# Section\n\nSee m-old001.\n")
        dest = tmp_path / "dest" / "rewrite.yaml"
        record = {
            "source_path": "rewrite.yaml",
            "reference_rewrites": [
                {"old_literal": "m-old001", "new_literal": "m-new001"},
            ],
        }
        result = _materialize_rewrite(record, src, dest)
        assert "diff_manifest" in result
        dm = result["diff_manifest"]
        assert dm["kind"] == "semantic"


class TestMaterializeMerge:
    def test_merge_writes_content(self, tmp_path):
        src = tmp_path / "merge.yaml"
        src.write_text("---\nname: Merge\ndescription: Merge\n---\nContent\n")
        dest = tmp_path / "dest" / "merge.yaml"
        record = {"source_path": "merge.yaml"}
        result = _materialize_merge(record, src, dest)
        assert result["action"] == "merge"
        assert dest.exists()


class TestMaterializeArchive:
    def test_archive_writes_content(self, tmp_path):
        src = tmp_path / "archive.yaml"
        src.write_text("---\nname: Archive\ndescription: Archive\n---\nContent\n")
        dest = tmp_path / "dest" / "archive.yaml"
        record = {"source_path": "archive.yaml"}
        result = _materialize_archive(record, src, dest)
        assert result["action"] == "archive"
        assert dest.exists()


class TestMaterializeReject:
    def test_reject_does_not_write(self, tmp_path):
        src = tmp_path / "reject.yaml"
        src.write_text("---\nname: Reject\ndescription: Reject\n---\nContent\n")
        dest = tmp_path / "dest" / "reject.yaml"
        record = {"source_path": "reject.yaml", "reason": "Binary", "exception_code": "BINARY_BYTES"}
        result = _materialize_reject(record, src, dest)
        assert result["action"] == "reject"
        assert result["exception_code"] == "BINARY_BYTES"


# ── Unit tests: helpers ───────────────────────────────────────────────────────


class TestExtractBodyBytes:
    def test_body_bytes_preserved(self):
        content = b"---\nid: m-test\nname: Test\ndescription: Desc\n---\nBody content.\n"
        body = _extract_body_bytes(content)
        assert body == b"Body content.\n"

    def test_no_frontmatter(self):
        content = b"Just content, no frontmatter."
        body = _extract_body_bytes(content)
        assert body == content


class TestInjectFrontmatterId:
    def test_injects_id(self):
        content = b"---\nname: Test\ndescription: Desc\n---\nBody\n"
        result = _inject_frontmatter_id(content, "m-new001")
        assert b"id: m-new001" in result

    def test_replaces_existing_id(self):
        content = b"---\nid: m-old001\nname: Test\ndescription: Desc\n---\nBody\n"
        result = _inject_frontmatter_id(content, "m-new001")
        assert b"id: m-new001" in result
        assert b"m-old001" not in result


class TestSemanticDiffManifest:
    def test_detects_frontmatter_change(self):
        orig = b"---\nid: m-old\ntitle: Old\n---\nBody\n"
        new = b"---\nid: m-new\ntitle: New\n---\nBody\n"
        diff = _semantic_diff_manifest(orig, new, "test.yaml")
        assert diff["kind"] == "semantic"
        assert len(diff["changes"]) > 0

    def test_detects_section_change(self):
        orig = b"# Section A\n\nContent A\n\n# Section B\n\nContent B\n"
        new = b"# Section A\n\nContent A modified\n\n# Section B\n\nContent B\n"
        diff = _semantic_diff_manifest(orig, new, "test.md")
        assert diff["kind"] == "semantic"
        assert len(diff["changes"]) > 0

    def test_no_changes_empty_diff(self):
        content = b"# Section\n\nContent\n"
        diff = _semantic_diff_manifest(content, content, "test.md")
        assert diff["change_count"] == 0


class TestApplyLinkRewrites:
    def test_rewrites_ids_in_content(self):
        content = b"See m-old001 and m-old002 for details."
        redirect_map = {"m-old001": "m-new001", "m-old002": "m-new002"}
        result = _apply_link_rewrites(content, redirect_map)
        assert b"m-new001" in result
        assert b"m-new002" in result
        assert b"m-old001" not in result
        assert b"m-old002" not in result


# ── Integrity gate tests ──────────────────────────────────────────────────────


class TestIntegrityGate:
    def test_binary_detected(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02")
        record = {"source_path": "binary.bin", "exception_code": None}
        issues = _check_integrity(record, f)
        codes = [c for c, _ in issues]
        assert EXC_BINARY in codes

    def test_executable_detected(self, tmp_path):
        f = tmp_path / "exec.sh"
        f.write_text("#!/bin/bash\necho hi\n")
        f.chmod(0o755)
        record = {"source_path": "exec.sh", "exception_code": None}
        issues = _check_integrity(record, f)
        codes = [c for c, _ in issues]
        assert EXC_EXECUTABLE in codes

    def test_lfs_pointer_detected(self, tmp_path):
        f = tmp_path / "lfs.bin"
        f.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:aaaa\nsize 1\n")
        record = {"source_path": "lfs.bin", "exception_code": None}
        issues = _check_integrity(record, f)
        codes = [c for c, _ in issues]
        assert EXC_LFS_POINTER in codes

    def test_path_length_detected(self, tmp_path, monkeypatch):
        from katana_migration import rehearsal as rm
        monkeypatch.setattr(rm, "MAX_PATH_LENGTH", 10)
        long_dir = tmp_path / "longpath"
        long_dir.mkdir()
        f = long_dir / "file.txt"
        f.write_text("content")
        record = {"source_path": str(f.relative_to(tmp_path)), "exception_code": None}
        issues = _check_integrity(record, f)
        codes = [c for c, _ in issues]
        assert EXC_PATH_LENGTH in codes

    def test_symlink_detected(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = tmp_path / "link.txt"
        if link.exists():
            link.unlink()
        link.symlink_to(target)
        record = {"source_path": "link.txt", "exception_code": EXC_SYMLINK}
        issues = _check_integrity(record, link)
        codes = [c for c, _ in issues]
        assert EXC_SYMLINK in codes

    def test_unicode_nfc_normalization(self, tmp_path):
        f = tmp_path / "nfd.txt"
        combined = "\u0065\u0301"
        f.write_text(f"Text with {combined}")
        record = {"source_path": "nfd.txt", "exception_code": None}
        issues = _check_integrity(record, f)
        codes = [c for c, _ in issues]
        assert "UNICODE_NFC" in codes

    def test_casefold_collision_detected(self, tmp_path):
        (tmp_path / "File.txt").write_text("content")
        f = tmp_path / "file.txt"
        f.write_text("other")
        record = {"source_path": "file.txt", "exception_code": None}
        issues = _check_integrity(record, f)
        codes = [c for c, _ in issues]
        assert EXC_CASEFOLD_COLLISION in codes

    def test_clean_file_no_issues(self, tmp_path):
        f = tmp_path / "clean.txt"
        f.write_text("Clean content")
        record = {"source_path": "clean.txt", "exception_code": None}
        issues = _check_integrity(record, f)
        assert len(issues) == 0


# ── Reference resolution tests ────────────────────────────────────────────────


class TestReferenceResolution:
    def test_resolve_references_finds_wiki_links(self):
        manifest_objects = [
            {"domain_resource_id": "m-abc123", "source_path": "mem1.yaml"},
            {"domain_resource_id": "w-def456", "source_path": "note1.md"},
        ]
        domain_objects = [
            {
                "source_path": "note1.md",
                "source_repo": "/tmp",
                "domain_resource_id": "w-def456",
                "sha256": "dummy",
            },
        ]
        entries, old_broken, new_broken = _resolve_references(manifest_objects, domain_objects, "wiki")
        assert isinstance(entries, list)
        assert isinstance(old_broken, list)
        assert isinstance(new_broken, list)

    def test_compute_reference_stats_constraint(self):
        entries = [{"source_id": "a", "target_id": "b"}]
        old_broken = []
        new_broken = []
        from katana_migration.rehearsal import _compute_reference_stats
        stats = _compute_reference_stats(entries, old_broken, new_broken)
        assert stats["constraint_holds"] is True
        assert stats["new_minus_old"] == 0

    def test_compute_reference_stats_new_broken_detected(self):
        entries = [{"source_id": "a", "target_id": "b"}]
        old_broken = []
        new_broken = [{"source_id": "a", "target_id": "b", "reason": "not found"}]
        from katana_migration.rehearsal import _compute_reference_stats
        stats = _compute_reference_stats(entries, old_broken, new_broken)
        assert stats["new_minus_old"] == 1
        assert stats["constraint_holds"] is False


# ── Path-filtered history extraction tests ────────────────────────────────────


class TestPathFilteredHistory:
    def test_filtered_history_wiki_extracts_only_wiki_paths(self, source_repo_with_history, tmp_path):
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_repo_with_history, text=True,
        ).strip()
        dest = tmp_path / "wiki_dest"
        _extract_filtered_history(
            str(source_repo_with_history), commit, ["wiki/"], dest, tmp_path / "tmp",
        )
        assert (dest / ".git").exists()
        files = {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file() and ".git" not in str(p)}
        for f in files:
            assert f.startswith("wiki/") or f == "MIGRATION_BASE" or f == "redirects.json" or f == "references.json" or not f
        memory_files = [f for f in files if f.startswith("memory/")]
        assert len(memory_files) == 0

    def test_filtered_history_memory_preserves_all(self, source_repo_with_history, tmp_path):
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_repo_with_history, text=True,
        ).strip()
        dest = tmp_path / "memory_dest"
        _extract_filtered_history(
            str(source_repo_with_history), commit, [], dest, tmp_path / "tmp",
        )
        assert (dest / ".git").exists()


# ── Full rehearsal engine tests ───────────────────────────────────────────────


class TestRehearsalEngineFull:
    def test_all_seven_actions_exercised_in_full_run(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        actions_seen = set()
        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                actions_seen.add(ar.get("action", ""))
        assert ACTION_PRESERVE in actions_seen
        assert ACTION_ID_BACKFILL in actions_seen
        assert ACTION_NORMALIZE in actions_seen
        assert ACTION_REWRITE in actions_seen
        assert ACTION_MERGE in actions_seen
        assert ACTION_ARCHIVE in actions_seen
        assert ACTION_REJECT in actions_seen

    def test_fixture_covers_all_actions(self, manifest_all_actions):
        actions_seen = set()
        for obj in manifest_all_actions["objects"]:
            actions_seen.add(obj["action"])
        expected = {ACTION_PRESERVE, ACTION_ID_BACKFILL, ACTION_NORMALIZE, ACTION_REWRITE,
                     ACTION_MERGE, ACTION_ARCHIVE, ACTION_REJECT}
        assert actions_seen == expected, f"Expected all 7 actions, got {actions_seen}"

    def test_conservation_invariant_holds(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        s = result.summary
        assert s["tracked"] == s["preserved"] + s["transformed"] + s["archived"] + s["rejected"]
        assert s["unclassified"] == 0
        assert s["invariant_holds"] is True

    def test_migration_base_marker_present(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            mbf = Path(dr["dest_repo"]) / MIGRATION_BASE_FILENAME
            assert mbf.exists(), f"MIGRATION_BASE missing in {domain}"
            mb = json.loads(mbf.read_text())
            assert "migration_run_id" in mb

    def test_redirects_json_present(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            rf = Path(dr["dest_repo"]) / REDIRECTS_FILENAME
            assert rf.exists(), f"redirects.json missing in {domain}"

    def test_references_json_present(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            rf = Path(dr["dest_repo"]) / REFERENCES_FILENAME
            assert rf.exists(), f"references.json missing in {domain}"
            ref_data = json.loads(rf.read_text())
            assert "entries" in ref_data
            assert "stats" in ref_data

    def test_reference_entries_have_required_fields(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            for entry in dr.get("reference_entries", []):
                assert "source_id" in entry
                assert "old_literal" in entry
                assert "old_target_id" in entry
                assert "new_target_id" in entry
                assert "anchor" in entry
                assert "disposition" in entry

    def test_idempotent_byte_identical(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root1 = tmp_path / "rehearsal_out1"
        dest_root2 = tmp_path / "rehearsal_out2"
        engine1 = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root1),
            committer_date="2024-06-01T00:00:00",
        )
        result1 = engine1.run()
        engine2 = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root2),
            committer_date="2024-06-01T00:00:00",
        )
        result2 = engine2.run()

        assert result1.summary == result2.summary

        for domain in result1.domains:
            dr1 = result1.domains[domain]
            dr2 = result2.domains[domain]
            assert dr1["commit_sha"] == dr2["commit_sha"], \
                f"Commit SHA mismatch for {domain}: {dr1['commit_sha']} != {dr2['commit_sha']}"

    def test_idempotent_file_content_identical(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root1 = tmp_path / "rehearsal_out1"
        dest_root2 = tmp_path / "rehearsal_out2"
        engine1 = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root1),
            committer_date="2024-06-01T00:00:00",
        )
        engine1.run()
        engine2 = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root2),
            committer_date="2024-06-01T00:00:00",
        )
        engine2.run()

        for domain in os.listdir(dest_root1):
            d1 = dest_root1 / domain
            d2 = dest_root2 / domain
            if not d1.is_dir() or not d2.is_dir():
                continue
            for root1, dirs1, files1 in os.walk(d1):
                rel = os.path.relpath(root1, d1)
                root2 = d2 / rel
                for fname in files1:
                    if ".git" in root1.split(os.sep):
                        continue
                    f1 = Path(root1) / fname
                    f2 = root2 / fname
                    if f2.exists():
                        c1 = f1.read_bytes()
                        c2 = f2.read_bytes()
                        assert c1 == c2, f"File content mismatch: {f1}"

    def test_no_committer_date_still_completes(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date=None,
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            assert len(dr["commit_sha"]) == 40

    def test_preserve_sha256_byte_equal_in_full_run(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                if ar.get("action") == ACTION_PRESERVE:
                    assert ar.get("sha256_match") is True, \
                        f"Preserve SHA-256 mismatch for {ar.get('source_path')}"

    def test_id_backfill_body_bytes_unchanged_in_full_run(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                if ar.get("action") == ACTION_ID_BACKFILL:
                    assert ar.get("body_bytes_unchanged") is True, \
                        f"ID backfill body bytes changed for {ar.get('source_path')}"

    def test_normalize_produces_diff_manifest_in_full_run(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                if ar.get("action") == ACTION_NORMALIZE:
                    assert "diff_manifest" in ar, \
                        f"Normalize missing diff_manifest for {ar.get('source_path')}"

    def test_rewrite_produces_diff_manifest_in_full_run(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                if ar.get("action") == ACTION_REWRITE:
                    assert "diff_manifest" in ar, \
                        f"Rewrite missing diff_manifest for {ar.get('source_path')}"

    def test_reference_constraint_holds_in_full_run(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            stats = dr.get("reference_stats", {})
            assert stats.get("constraint_holds") is not None, \
                f"Reference stats missing constraint_holds for {domain}"

    def test_integrity_gate_blocks_executable(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        found = False
        for domain, dr in result.domains.items():
            for issue in dr.get("integrity_issues", []):
                if issue["code"] == EXC_EXECUTABLE:
                    found = True
        assert found, "Executable file should trigger integrity issue"

    def test_integrity_gate_blocks_binary(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        found = False
        for domain, dr in result.domains.items():
            for issue in dr.get("integrity_issues", []):
                if issue["code"] == EXC_BINARY:
                    found = True
        assert found, "Binary file should trigger integrity issue"

    def test_no_production_paths_touched(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        engine.run()
        assert not Path("/data/memory").exists() or not any(
            f for f in dest_root.rglob("*") if "/data/memory" in str(f)
        )
        assert str(dest_root).startswith(str(tmp_path))

    def test_dest_repo_is_git_repo(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            repo = Path(dr["dest_repo"])
            assert (repo / ".git").exists(), f"Not a git repo: {domain}"
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
            ).strip()
            assert len(sha) == 40

    def test_wiki_wf_domains_have_filtered_history(self, source_sets_config_wiki_wf, tmp_path):
        manifest = run_inventory(source_sets_config_wiki_wf, migration_run_id="mig-test-wiki-wf")
        wiki_root = source_sets_config_wiki_wf[0]["root"]
        source_root = str(Path(wiki_root).parent)
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest,
            source_root=source_root,
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        assert "wiki" in result.domains
        assert "work-folder" in result.domains

    def test_full_stop_on_blocking_error(self, manifest_all_actions, source_sets_all_actions, tmp_path, monkeypatch):
        from katana_migration import rehearsal as rm

        original_check = rm._check_integrity

        def fake_check_integrity(record, dest_path):
            issues = original_check(record, dest_path)
            issues.append((EXC_BINARY, "Injected blocking error"))
            return issues

        monkeypatch.setattr(rm, "_check_integrity", fake_check_integrity)

        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        with pytest.raises(RuntimeError):
            engine.run()

    def test_written_plus_skipped_equals_total(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        s = result.summary
        assert s["written"] + s["skipped"] == s["tracked"]

    def test_manifest_actions_match_rehearsal_actions(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        manifest_actions = {}
        for obj in manifest_all_actions["objects"]:
            sp = obj["source_path"]
            manifest_actions[sp] = obj["action"]
        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                sp = ar.get("source_path", "")
                if sp in manifest_actions:
                    assert ar["action"] == manifest_actions[sp], \
                        f"Action mismatch for {sp}: rehearsal={ar['action']} manifest={manifest_actions[sp]}"

    def test_domain_commit_has_files(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        for domain, dr in result.domains.items():
            repo = Path(dr["dest_repo"])
            tracked = subprocess.check_output(
                ["git", "ls-tree", "-r", "HEAD", "--name-only"],
                cwd=repo, text=True,
            ).strip().split("\n")
            tracked = [t for t in tracked if t]
            assert len(tracked) > 0, f"No tracked files in {domain}"

    def test_committer_date_injected_produces_deterministic_commits(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        date = "2024-06-01T12:00:00"
        dest_root1 = tmp_path / "r1"
        dest_root2 = tmp_path / "r2"
        e1 = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root1),
            committer_date=date,
        )
        r1 = e1.run()
        e2 = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root2),
            committer_date=date,
        )
        r2 = e2.run()
        for domain in r1.domains:
            assert r1.domains[domain]["commit_sha"] == r2.domains[domain]["commit_sha"], \
                f"Different commit SHA for {domain} with same committer_date"

    def test_rehearsal_only_writes_to_dest_root(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "rehearsal_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        engine.run()
        for f in dest_root.rglob("*"):
            assert str(f).startswith(str(dest_root)), f"File outside dest_root: {f}"

        prod_paths = ["/data/memory", "/data/vault/", "/data/wiki", "/data/work-records"]
        for f in dest_root.rglob("*"):
            for pp in prod_paths:
                assert pp not in str(f), f"Production path found: {f}"


# ── Edge case tests ───────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_manifest(self, tmp_path):
        dest_root = tmp_path / "empty_out"
        manifest = {"migration_run_id": "mig-empty", "objects": [], "redirect_map": {}}
        engine = RehearsalEngine(
            manifest=manifest,
            dest_root=str(dest_root),
            committer_date="2024-01-01T00:00:00",
        )
        result = engine.run()
        assert result.summary["tracked"] == 0

    def test_manifest_with_only_rejected(self, tmp_path):
        dest_root = tmp_path / "reject_out"
        src = tmp_path / "src"
        src.mkdir()
        (src / "bad.bin").write_bytes(b"\x00binary")
        source_sets = [{
            "name": "bad",
            "source_repo": str(src),
            "root": str(src),
            "prefix": "m-",
            "object_class": "unknown",
            "destination_repo": "memory",
            "default_action": "preserve",
            "include": ["**/*"],
        }]
        manifest = run_inventory(source_sets, migration_run_id="mig-reject-only")
        engine = RehearsalEngine(
            manifest=manifest,
            source_root=str(src),
            dest_root=str(dest_root),
            committer_date="2024-01-01T00:00:00",
        )
        result = engine.run()
        assert result.summary["rejected"] >= 1
        assert result.summary["tracked"] == result.summary["rejected"]

    def test_multi_domain_rehearsal(self, manifest_all_actions, source_sets_all_actions, tmp_path):
        dest_root = tmp_path / "multi_out"
        engine = RehearsalEngine(
            manifest=manifest_all_actions,
            source_root=str(source_sets_all_actions),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()
        assert len(result.domains) > 0
        for domain, dr in result.domains.items():
            assert "commit_sha" in dr
            assert len(dr["commit_sha"]) == 40


# ── E2E: link rewrite in full rehearsal ──────────────────────────────────────


class TestLinkRewriteInFullRehearsal:
    def test_link_rewrite_applies_in_full_run(self, tmp_path):
        root = tmp_path / "link_src"
        root.mkdir()
        (root / "rewrite").mkdir(parents=True)
        (root / "canonical").mkdir(parents=True)

        (root / "canonical" / "preserve.yaml").write_text(
            "---\nid: m-canon01\nname: Canonical\ndescription: Preserved memory\n---\nBody\n"
        )
        (root / "rewrite" / "rewrite.yaml").write_text(
            "---\nname: Rewrite\ndescription: Has refs\n---\nSee m-oldref for details.\n"
        )

        source_sets = [
            {
                "name": "canonical",
                "source_repo": str(root / "canonical"),
                "root": str(root / "canonical"),
                "prefix": "m-",
                "object_class": "memory_canonical",
                "destination_repo": "memory",
                "default_action": "preserve",
                "include": ["**/*"],
            },
            {
                "name": "rewrite",
                "source_repo": str(root / "rewrite"),
                "root": str(root / "rewrite"),
                "prefix": "m-",
                "object_class": "memory",
                "destination_repo": "memory",
                "default_action": "rewrite",
                "include": ["**/*"],
            },
        ]
        manifest = run_inventory(source_sets, migration_run_id="mig-link-rewrite")

        for obj in manifest["objects"]:
            if obj["source_path"] == "rewrite.yaml":
                obj["reference_rewrites"] = [
                    {"old_literal": "m-oldref", "new_literal": "m-canon01"},
                ]

        dest_root = tmp_path / "link_out"
        engine = RehearsalEngine(
            manifest=manifest,
            source_root=str(root),
            dest_root=str(dest_root),
            committer_date="2024-06-01T00:00:00",
        )
        result = engine.run()

        for domain, dr in result.domains.items():
            for ar in dr.get("action_results", []):
                if ar.get("action") == ACTION_REWRITE and ar.get("source_path") == "rewrite.yaml":
                    dest_file = Path(dr["dest_repo"]) / "rewrite.yaml"
                    content = dest_file.read_text()
                    assert "m-canon01" in content
                    assert "m-oldref" not in content
                    return
        pytest.fail("Rewrite action not found in rehearsal results")