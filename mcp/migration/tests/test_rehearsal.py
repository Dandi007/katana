"""Contract tests for migration rehearsal engine (M3b REHEARSED phase)."""

import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

import pytest

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
    run_inventory,
    sha256_hex,
)
from katana_migration.rehearsal import (
    MIGRATION_BASE_FILENAME,
    REDIRECTS_FILENAME,
    REFERENCES_FILENAME,
    RehearsalEngine,
    _check_integrity,
    _extract_body_bytes,
    _inject_frontmatter_id,
    _materialize_archive,
    _materialize_id_backfill,
    _materialize_merge,
    _materialize_normalize,
    _materialize_preserve,
    _materialize_rewrite,
    run_rehearsal,
)


# ── Fixtures: source tree with all action types ───────────────────────────────

@pytest.fixture
def source_root_three_domains(tmp_path):
    """Controlled fixtures: memory, wiki, work-folder with all action types."""
    root = tmp_path / "source"
    root.mkdir()

    # ── Memory: canonical (preserve) + legacy (id_backfill) ──
    mem_dir = root / "data" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "alice").mkdir()
    (mem_dir / "alice" / "card1.md").write_text(
        "---\nid: m-a1b2c3\nname: card-one\ndescription: desc one\nstatus: active\nlast_verified: 2026-07-08\n---\n\n## Fact\nContent A\n",
        encoding="utf-8",
    )
    (mem_dir / "bob").mkdir()
    (mem_dir / "bob" / "card2.md").write_text(
        "---\nid: m-d4e5f6\nname: card-two\ndescription: desc two\nstatus: active\nlast_verified: 2026-07-08\n---\n\n## Fact\nContent B ref m-a1b2c3\n",
        encoding="utf-8",
    )

    legacy_dir = root / "data" / "vault" / "memory"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "legacy1.md").write_text(
        "---\nname: legacy-one\ndescription: legacy desc\n---\n\n## Fact\nLegacy content\n",
        encoding="utf-8",
    )

    # ── Wiki: writable, raw, schema ──
    wiki_dir = root / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "Zettelkasten").mkdir()
    (wiki_dir / "Zettelkasten" / "note1.md").write_text(
        "# Zettelkasten Note\n\nRef: [[m-a1b2c3]]\n",
        encoding="utf-8",
    )
    (wiki_dir / "转换文档").mkdir()
    (wiki_dir / "转换文档" / "raw1.md").write_text(
        "# Raw Document\n\nRef: [[card-one]]\n",
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

    # ── Work folder ──
    wf_dir = root / "智元工作" / "工作记录"
    wf_dir.mkdir(parents=True)
    (wf_dir / "rec1.md").write_text(
        "# Work Record\n\nRecord content\n",
        encoding="utf-8",
    )

    # ── Exception fixtures ──
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

    long_name = "a" * 260
    try:
        (exc_dir / f"{long_name}.md").write_text("long basename\n", encoding="utf-8")
    except OSError:
        pass

    return root


@pytest.fixture
def source_sets_config(source_root_three_domains):
    root = source_root_three_domains
    return [
        {
            "name": "memory_canonical",
            "root": str(root / "data" / "memory"),
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
            "root": str(root / "data" / "vault" / "memory"),
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
            "root": str(root / "wiki"),
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
            "root": str(root / "智元工作" / "工作记录"),
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
            "root": str(root / "exceptions"),
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
    return run_inventory(source_sets_config, migration_run_id="test-rehearsal-001")


@pytest.fixture
def dest_root(tmp_path):
    return tmp_path / "dest"


# ── Full rehearsal run ────────────────────────────────────────────────────────

@pytest.fixture
def rehearsal_result(manifest, source_root_three_domains, dest_root):
    return run_rehearsal(
        manifest=manifest,
        source_root=str(source_root_three_domains),
        dest_root=str(dest_root),
    )


# ── Action: preserve (SHA-256 byte-equal) ─────────────────────────────────────

def test_preserve_byte_equal(manifest, source_root_three_domains, tmp_path):
    domain_root = str(tmp_path / "preserve_test")
    os.makedirs(domain_root, exist_ok=True)

    _roots = _build_root_map(manifest)
    preserve_objs = [o for o in manifest["objects"] if o["action"] == ACTION_PRESERVE]

    for obj in preserve_objs:
        src_root = _roots.get(obj.get("source_repo", ""), str(source_root_three_domains))
        result = _materialize_preserve(obj, src_root, domain_root)
        assert result["written"] is True, f"Failed to preserve: {obj['source_path']}"
        assert result.get("byte_equal") is True, (
            f"SHA-256 mismatch for {obj['source_path']}: "
            f"expected {obj.get('sha256')}, got {result.get('sha256')}"
        )
        dest_path = os.path.join(domain_root, obj["destination_path"])
        assert os.path.exists(dest_path), f"Destination file not found: {dest_path}"


def test_preserve_sha256_matches_source(manifest, source_root_three_domains, tmp_path):
    domain_root = str(tmp_path / "sha256_test")
    os.makedirs(domain_root, exist_ok=True)

    _roots = _build_root_map(manifest)
    preserve_objs = [o for o in manifest["objects"] if o["action"] == ACTION_PRESERVE]
    assert len(preserve_objs) > 0, "No preserve objects to test"

    for obj in preserve_objs:
        src_root = _roots.get(obj.get("source_repo", ""), str(source_root_three_domains))
        result = _materialize_preserve(obj, src_root, domain_root)
        src_content = (Path(src_root) / obj["source_path"]).read_bytes()
        dst_content = Path(os.path.join(domain_root, obj["destination_path"])).read_bytes()

        assert sha256_hex(src_content) == sha256_hex(dst_content), (
            f"Content differs for {obj['source_path']}"
        )
        assert result["sha256"] == sha256_hex(src_content)


def _build_root_map(manifest):
    return {ss.get("source_repo", ""): ss.get("root", "") for ss in manifest.get("source_sets", [])}


# ── Action: id_backfill (body bytes unchanged) ─────────────────────────────────

def test_id_backfill_body_bytes_unchanged(manifest, source_root_three_domains, tmp_path):
    domain_root = str(tmp_path / "backfill_test")
    os.makedirs(domain_root, exist_ok=True)

    _roots = _build_root_map(manifest)
    backfill_objs = [o for o in manifest["objects"] if o["action"] == ACTION_ID_BACKFILL]

    for obj in backfill_objs:
        src_root = _roots.get(obj.get("source_repo", ""), str(source_root_three_domains))
        result = _materialize_id_backfill(obj, src_root, domain_root)
        assert result["written"] is True, f"Failed to backfill: {obj['source_path']}"
        assert result.get("body_bytes_unchanged") is True, (
            f"Body bytes changed for {obj['source_path']}"
        )
        assert result.get("injected_id") is not None
        assert result["injected_id"].startswith("m-")

        dest_path = os.path.join(domain_root, obj["destination_path"])
        content = Path(dest_path).read_text()
        assert f"id: {result['injected_id']}" in content, "ID not injected into frontmatter"


def test_id_backfill_injects_id_into_frontmatter(manifest, source_root_three_domains, tmp_path):
    domain_root = str(tmp_path / "inject_test")
    os.makedirs(domain_root, exist_ok=True)

    _roots = _build_root_map(manifest)
    backfill_objs = [o for o in manifest["objects"] if o["action"] == ACTION_ID_BACKFILL]
    assert len(backfill_objs) > 0, "No backfill objects to test"

    for obj in backfill_objs:
        src_root = _roots.get(obj.get("source_repo", ""), str(source_root_three_domains))
        result = _materialize_id_backfill(obj, src_root, domain_root)
        dest_content = Path(os.path.join(domain_root, obj["destination_path"])).read_text()
        assert dest_content.startswith("---\n"), "Frontmatter not preserved"
        rid = obj.get("domain_resource_id") or obj.get("vfs_node_id")
        assert f"id: {rid}" in dest_content, "ID not found in frontmatter"


# ── Action: normalize ─────────────────────────────────────────────────────────

def test_normalize_produces_nfc_text(manifest, source_root_three_domains, tmp_path):
    domain_root = str(tmp_path / "normalize_test")
    os.makedirs(domain_root, exist_ok=True)

    wiki_objs = [o for o in manifest["objects"] if o["object_class"].startswith("wiki_")]
    for obj in wiki_objs:
        result = _materialize_normalize(
            obj, str(source_root_three_domains), domain_root,
        )
        if result.get("written"):
            dest_path = os.path.join(domain_root, obj["destination_path"])
            content = Path(dest_path).read_text()
            import unicodedata
            assert unicodedata.normalize("NFC", content) == content, "Content not NFC-normalized"


def test_normalize_crlf_to_lf(tmp_path):
    root = tmp_path / "crlf_source"
    root.mkdir()
    (root / "file.md").write_text("line1\r\nline2\r\n", encoding="utf-8")

    obj = {
        "source_path": "file.md",
        "destination_path": "file.md",
        "sha256": sha256_hex(b"line1\r\nline2\r\n"),
        "domain_resource_id": "w-000001",
    }
    dest = str(tmp_path / "crlf_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_normalize(obj, str(root), dest)
    assert result["written"] is True

    content = Path(os.path.join(dest, "file.md")).read_text()
    assert "\r\n" not in content
    assert "\r" not in content
    assert content.endswith("\n")


def test_normalize_trailing_whitespace_stripped(tmp_path):
    root = tmp_path / "ws_source"
    root.mkdir()
    (root / "file.md").write_text("line1   \nline2\t\n", encoding="utf-8")

    obj = {
        "source_path": "file.md",
        "destination_path": "file.md",
        "sha256": sha256_hex(b"line1   \nline2\t\n"),
        "domain_resource_id": "w-000002",
    }
    dest = str(tmp_path / "ws_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_normalize(obj, str(root), dest)
    content = Path(os.path.join(dest, "file.md")).read_text()
    for line in content.split("\n"):
        if line:
            assert line == line.rstrip(), f"Trailing whitespace not stripped: {repr(line)}"


# ── Action: rewrite ───────────────────────────────────────────────────────────

def test_rewrite_replaces_references(tmp_path):
    root = tmp_path / "rewrite_source"
    root.mkdir()
    (root / "file.md").write_text("# Title\n\nSee m-oldref for details.\n", encoding="utf-8")

    obj = {
        "source_path": "file.md",
        "destination_path": "file.md",
        "sha256": sha256_hex(b"# Title\n\nSee m-oldref for details.\n"),
        "domain_resource_id": "w-000003",
        "reference_rewrites": [
            {"old_literal": "m-oldref", "new_literal": "m-newref"},
        ],
    }
    dest = str(tmp_path / "rewrite_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_rewrite(obj, str(root), dest)
    assert result["written"] is True

    content = Path(os.path.join(dest, "file.md")).read_text()
    assert "m-newref" in content
    assert "m-oldref" not in content


# ── Action: archive ───────────────────────────────────────────────────────────

def test_archive_moves_to_archive_dir(tmp_path):
    root = tmp_path / "archive_source"
    root.mkdir()
    (root / "old.md").write_text("archive this\n", encoding="utf-8")

    obj = {
        "source_path": "old.md",
        "destination_path": "old.md",
        "sha256": sha256_hex(b"archive this\n"),
        "domain_resource_id": "m-000001",
    }
    dest = str(tmp_path / "archive_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_archive(obj, str(root), dest)
    assert result["written"] is True
    assert result.get("archived_path") == "_archive/old.md"

    archived_path = os.path.join(dest, "_archive", "old.md")
    assert os.path.exists(archived_path)
    assert Path(archived_path).read_text() == "archive this\n"


# ── Action: merge ─────────────────────────────────────────────────────────────

def test_merge_combines_content(tmp_path):
    root = tmp_path / "merge_source"
    root.mkdir()
    (root / "existing.md").write_text("line a\nline b\n", encoding="utf-8")

    dest = str(tmp_path / "merge_dest")
    os.makedirs(dest, exist_ok=True)
    Path(os.path.join(dest, "existing.md")).write_text("line a\nline c\n", encoding="utf-8")

    obj = {
        "source_path": "existing.md",
        "destination_path": "existing.md",
        "sha256": sha256_hex(b"line a\nline b\n"),
        "domain_resource_id": "m-000002",
    }

    result = _materialize_merge(obj, str(root), dest)
    assert result["written"] is True

    merged = Path(os.path.join(dest, "existing.md")).read_text()
    assert "line a" in merged
    assert "line c" in merged
    assert "line b" in merged


# ── Full rehearsal: invariants ─────────────────────────────────────────────────

def test_rehearsal_total_conservation(rehearsal_result):
    s = rehearsal_result["summary"]
    assert s["written"] <= s["total_objects"], "Written > total objects"
    assert s["written"] + s["skipped"] == s["total_objects"], (
        f"written ({s['written']}) + skipped ({s['skipped']}) != total ({s['total_objects']})"
    )


def test_rehearsal_domain_results_present(rehearsal_result):
    domain_results = rehearsal_result.get("domain_results", {})
    assert len(domain_results) > 0, "No domain results"


def test_rehearsal_migration_base_created(rehearsal_result, dest_root):
    for domain in rehearsal_result.get("domain_results", {}):
        marker_path = os.path.join(str(dest_root), domain, MIGRATION_BASE_FILENAME)
        if os.path.exists(marker_path):
            marker = json.loads(Path(marker_path).read_text())
            assert marker["migration_run_id"] == "test-rehearsal-001"
            assert marker["phase"] == "REHEARSED"
            assert marker["domain"] == domain


def test_rehearsal_redirects_created(rehearsal_result, dest_root):
    for domain in rehearsal_result.get("domain_results", {}):
        redirects_path = os.path.join(str(dest_root), domain, REDIRECTS_FILENAME)
        if os.path.exists(redirects_path):
            redirects = json.loads(Path(redirects_path).read_text())
            assert isinstance(redirects, dict)


def test_rehearsal_references_created(rehearsal_result, dest_root):
    for domain in rehearsal_result.get("domain_results", {}):
        refs_path = os.path.join(str(dest_root), domain, REFERENCES_FILENAME)
        if os.path.exists(refs_path):
            refs = json.loads(Path(refs_path).read_text())
            assert isinstance(refs, list)


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_rehearsal_idempotent_byte_identical(manifest, source_root_three_domains, tmp_path):
    dest1 = str(tmp_path / "idem1")
    dest2 = str(tmp_path / "idem2")

    r1 = run_rehearsal(manifest=manifest, source_root=str(source_root_three_domains), dest_root=dest1)
    r2 = run_rehearsal(manifest=manifest, source_root=str(source_root_three_domains), dest_root=dest2)

    assert r1["summary"] == r2["summary"], "Summary differs on rerun"
    assert r1["reference_stats"] == r2["reference_stats"], "Reference stats differ on rerun"
    assert r1["idempotent"] == r2["idempotent"]
    assert len(r1["errors"]) == len(r2["errors"])

    for domain in r1.get("domain_results", {}):
        domain_dir1 = os.path.join(dest1, domain)
        domain_dir2 = os.path.join(dest2, domain)
        if os.path.isdir(domain_dir1):
            _assert_dir_trees_equal(domain_dir1, domain_dir2)


def _assert_dir_trees_equal(dir1, dir2):
    files1 = sorted(_list_files(dir1))
    files2 = sorted(_list_files(dir2))
    assert files1 == files2, f"File lists differ: {set(files1) ^ set(files2)}"
    for f in files1:
        p1 = os.path.join(dir1, f)
        p2 = os.path.join(dir2, f)
        if os.path.isfile(p1) and os.path.isfile(p2):
            assert Path(p1).read_bytes() == Path(p2).read_bytes(), f"Content differs: {f}"
        elif os.path.isdir(p1) and os.path.isdir(p2):
            pass


def _list_files(root):
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        if ".git" in dirpath:
            continue
        for fn in filenames:
            result.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return result


def test_rehearsal_same_manifest_same_result(manifest, source_root_three_domains, tmp_path):
    dest1 = str(tmp_path / "same1")
    dest2 = str(tmp_path / "same2")

    engine1 = RehearsalEngine(manifest=manifest, source_root=str(source_root_three_domains), dest_root=dest1)
    r1 = engine1.run()

    engine2 = RehearsalEngine(manifest=manifest, source_root=str(source_root_three_domains), dest_root=dest2)
    r2 = engine2.run()

    assert r1["summary"] == r2["summary"], "Summary differs on rerun"
    assert r1["idempotent"] == r2["idempotent"]


# ── Integrity gate: executable ────────────────────────────────────────────────

def test_integrity_executable_rejected(tmp_path):
    root = tmp_path / "exec_test"
    root.mkdir()
    exe = root / "script.sh"
    exe.write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    exe.chmod(0o755)

    obj = {
        "source_path": "script.sh",
        "destination_path": "script.sh",
        "sha256": sha256_hex(b"#!/bin/bash\necho hi\n"),
        "domain_resource_id": "m-000003",
    }
    dest = str(tmp_path / "exec_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_preserve(obj, str(root), dest)
    assert result["written"] is True

    from katana_migration.rehearsal import _check_executable
    exec_issue = _check_executable(Path(dest) / "script.sh", obj)
    assert exec_issue is not None
    assert exec_issue[0] == EXC_EXECUTABLE


# ── Integrity gate: binary ─────────────────────────────────────────────────────

def test_integrity_binary_detected():
    content = b"\x00\x01\x02\x03"
    obj = {"source_path": "bin.bin", "destination_path": "bin.bin"}
    issues = _check_integrity(obj, content, "/tmp")
    codes = [c for c, _ in issues]
    assert EXC_BINARY in codes


# ── Integrity gate: LFS pointer ────────────────────────────────────────────────

def test_integrity_lfs_pointer_detected():
    content = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n"
    obj = {"source_path": "lfs.md", "destination_path": "lfs.md"}
    issues = _check_integrity(obj, content, "/tmp")
    codes = [c for c, _ in issues]
    assert EXC_LFS_POINTER in codes


# ── Integrity gate: symlink ───────────────────────────────────────────────────

def test_integrity_symlink_not_dereferenced_in_manifest(manifest):
    symlinks = [o for o in manifest["objects"] if o["exception_code"] == EXC_SYMLINK]
    for obj in symlinks:
        assert obj["sha256"] is None, "Symlink must not be dereferenced"
        assert obj["git_blob_oid"] is None, "Symlink must not be dereferenced"


# ── Integrity gate: path length ───────────────────────────────────────────────

def test_integrity_path_length_long_basename():
    long_name = "a" * 260
    obj = {"source_path": long_name + ".md", "destination_path": long_name + ".md"}
    content = b"test"
    issues = _check_integrity(obj, content, "/tmp")
    codes = [c for c, _ in issues]
    assert EXC_PATH_LENGTH in codes


# ── Body extraction helper ────────────────────────────────────────────────────

def test_extract_body_bytes_preserves_body():
    card = "---\nid: m-abc123\nname: test\ndescription: test\n---\n\n## Fact\nBody content\n"
    content = card.encode("utf-8")
    body = _extract_body_bytes(content)
    assert b"## Fact\nBody content\n" in body


def test_inject_frontmatter_id_adds_missing():
    content = "---\nname: test\ndescription: test\n---\n\n## Fact\nBody content\n".encode("utf-8")
    result = _inject_frontmatter_id(content, "m-new001")
    assert b"id: m-new001" in result
    body = _extract_body_bytes(result)
    assert b"Body content\n" in body


def test_inject_frontmatter_id_replaces_existing():
    content = "---\nid: m-old\nname: test\n---\n\n## Fact\nBody content\n".encode("utf-8")
    result = _inject_frontmatter_id(content, "m-new001")
    assert b"id: m-new001" in result
    assert b"id: m-old" not in result
    body = _extract_body_bytes(result)
    assert b"Body content\n" in body


# ── Reference stats: new broken - old broken = 0 ──────────────────────────────

def test_reference_constraint_holds(rehearsal_result):
    stats = rehearsal_result.get("reference_stats", {})
    old_broken = stats.get("old_broken", 0)
    new_broken = stats.get("new_broken", 0)
    assert new_broken - old_broken == 0, (
        f"Reference constraint violated: new broken ({new_broken}) "
        f"- old broken ({old_broken}) = {new_broken - old_broken}"
    )
    assert stats.get("constraint_holds") is True


# ── No production paths touched ───────────────────────────────────────────────

def test_rehearsal_no_production_paths(rehearsal_result):
    dest_root = rehearsal_result.get("dest_root", "")
    assert dest_root, "No dest_root in result"
    assert not dest_root.startswith("/data/memory")
    assert not dest_root.startswith("/data/vault")
    assert not dest_root.startswith("/data/wiki")
    assert not dest_root.startswith("/data/work-records")


def test_rehearsal_dest_is_temp(rehearsal_result):
    dest_root = rehearsal_result.get("dest_root", "")
    assert dest_root, "No dest_root in result"
    assert "/tmp" in dest_root or "tmp" in dest_root, f"Destination not in temp: {dest_root}"


# ── Rehearsal engine: per-action materialization ──────────────────────────────

def test_rehearsal_preserve_objects_written(rehearsal_result):
    results = rehearsal_result.get("results", [])
    preserve_results = [r for r in results if r.get("action") == ACTION_PRESERVE]
    for r in preserve_results:
        assert r.get("written") is True, f"Preserve object not written: {r.get('source_path')}"


def test_rehearsal_id_backfill_objects_written(rehearsal_result):
    results = rehearsal_result.get("results", [])
    backfill_results = [r for r in results if r.get("action") == ACTION_ID_BACKFILL]
    for r in backfill_results:
        assert r.get("written") is True, f"Backfill object not written: {r.get('source_path')}"


def test_rehearsal_rejected_objects_not_written(rehearsal_result):
    results = rehearsal_result.get("results", [])
    reject_results = [r for r in results if r.get("action") == ACTION_REJECT]
    for r in reject_results:
        assert r.get("written") is False, f"Rejected object was written: {r.get('source_path')}"


# ── Rehearsal engine: error handling ──────────────────────────────────────────

def test_rehearsal_blocking_exception_stops(tmp_path):
    root = tmp_path / "blocking_source"
    root.mkdir()
    (root / "good.md").write_text("# Good\n", encoding="utf-8")
    (root / "bad.bin").write_bytes(b"\x00\x01\x02")

    config = [{
        "name": "blocking",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["**/*"],
    }]
    manifest = run_inventory(config, migration_run_id="blocking-test")

    bad_obj = [o for o in manifest["objects"] if o["source_path"] == "bad.bin"]
    assert len(bad_obj) == 1
    assert bad_obj[0]["action"] == ACTION_REJECT
    assert bad_obj[0]["exception_code"] == EXC_BINARY

    dest = str(tmp_path / "blocking_dest")
    result = run_rehearsal(manifest=manifest, source_root=str(root), dest_root=dest)

    rejected_objs = [o for o in manifest["objects"] if o["action"] == ACTION_REJECT]
    for obj in rejected_objs:
        assert not any(
            r.get("source_path") == obj["source_path"] and r.get("written")
            for r in result.get("results", [])
        ), f"Rejected object was written: {obj['source_path']}"

    preserved = [o for o in manifest["objects"] if o["action"] == ACTION_PRESERVE]
    for obj in preserved:
        assert any(
            r.get("source_path") == obj["source_path"] and r.get("written")
            for r in result.get("results", [])
        ), f"Preserved object not written: {obj['source_path']}"


# ── Rehearsal engine: git repo creation ───────────────────────────────────────

def test_rehearsal_creates_git_repos(rehearsal_result, dest_root):
    for domain in rehearsal_result.get("domain_results", {}):
        git_dir = os.path.join(str(dest_root), domain, ".git")
        assert os.path.isdir(git_dir), f"No .git in {domain}"


# ── Rehearsal engine: domain commits ──────────────────────────────────────────

def test_rehearsal_domain_commits_have_sha(rehearsal_result):
    for domain, info in rehearsal_result.get("domain_results", {}).items():
        commit = info.get("commit", {})
        if commit.get("committed"):
            assert commit.get("detail"), f"No commit SHA for {domain}"
            assert len(commit["detail"]) == 40, f"Invalid SHA: {commit['detail']}"


# ── Rehearsal engine: fixture covers all action types ─────────────────────────

def test_fixture_covers_all_actions(manifest):
    actions = {r["action"] for r in manifest["objects"]}
    expected = {ACTION_PRESERVE, ACTION_ID_BACKFILL, ACTION_REJECT}
    for action in expected:
        assert action in actions, f"Fixture missing action: {action}"


# ── Rehearsal engine: object_class classification ─────────────────────────────

def test_rehearsal_domain_grouping(rehearsal_result):
    domain_results = rehearsal_result.get("domain_results", {})
    assert "memory" in domain_results, "No memory domain result"
    assert "wiki" in domain_results, "No wiki domain result"
    assert "work_folder" in domain_results, "No work_folder domain result"


# ── Rehearsal engine: diff manifest for normalize/rewrite ─────────────────────

def test_normalize_diff_manifest_present(tmp_path):
    root = tmp_path / "diff_source"
    root.mkdir()
    (root / "file.md").write_text("line1\r\nline2\r\n", encoding="utf-8")

    obj = {
        "source_path": "file.md",
        "destination_path": "file.md",
        "sha256": sha256_hex(b"line1\r\nline2\r\n"),
        "domain_resource_id": "w-000010",
    }
    dest = str(tmp_path / "diff_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_normalize(obj, str(root), dest)
    diff = result.get("diff_manifest", {})
    assert diff.get("changed") is True, "CRLF normalization should produce diff"
    assert len(diff.get("changes", [])) > 0


def test_rewrite_diff_manifest_present(tmp_path):
    root = tmp_path / "rewrite_diff_source"
    root.mkdir()
    (root / "file.md").write_text("See m-oldref\n", encoding="utf-8")

    obj = {
        "source_path": "file.md",
        "destination_path": "file.md",
        "sha256": sha256_hex(b"See m-oldref\n"),
        "domain_resource_id": "w-000011",
        "reference_rewrites": [
            {"old_literal": "m-oldref", "new_literal": "m-newref"},
        ],
    }
    dest = str(tmp_path / "rewrite_diff_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_rewrite(obj, str(root), dest)
    diff = result.get("diff_manifest", {})
    assert diff.get("changed") is True, "Rewrite should produce diff"


# ── Rehearsal engine: Manifest file not found handling ────────────────────────

def test_preserve_file_not_found(tmp_path):
    obj = {
        "source_path": "nonexistent.md",
        "destination_path": "nonexistent.md",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    }
    dest = str(tmp_path / "missing_dest")
    os.makedirs(dest, exist_ok=True)

    result = _materialize_preserve(obj, str(tmp_path), dest)
    assert result["written"] is False
    assert "reason" in result


# ── Rehearsal engine: empty manifest ──────────────────────────────────────────

def test_rehearsal_empty_manifest(tmp_path):
    manifest = {
        "migration_run_id": "empty-test",
        "source_sets": [],
        "objects": [],
        "redirect_map": {},
        "summary": {
            "tracked": 0,
            "preserved": 0,
            "transformed": 0,
            "archived": 0,
            "rejected": 0,
            "unclassified": 0,
            "invariant_holds": True,
        },
    }
    dest = str(tmp_path / "empty_dest")
    result = run_rehearsal(manifest=manifest, source_root=str(tmp_path), dest_root=dest)

    assert result["summary"]["total_objects"] == 0
    assert result["summary"]["written"] == 0
    assert result["idempotent"] is True


# ── Rehearsal engine: duplicative basename processing ─────────────────────────

def test_duplicate_basename_rejected_in_manifest(tmp_path):
    root = tmp_path / "dup_test"
    dir1 = root / "dir1"
    dir2 = root / "dir2"
    dir1.mkdir(parents=True)
    dir2.mkdir(parents=True)
    (dir1 / "note.md").write_text("content one", encoding="utf-8")
    (dir2 / "note.md").write_text("content two different", encoding="utf-8")

    config = [{
        "name": "dup",
        "root": str(root),
        "source_repo": "/test",
        "source_commit": "0000000000000000000000000000000000000000",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }]
    manifest = run_inventory(config, migration_run_id="dup-test")

    dup_objs = [o for o in manifest["objects"] if o["action"] == ACTION_REJECT and "Duplicate basename" in (o.get("reason") or "")]
    assert len(dup_objs) >= 1, "Duplicate basenames not detected"

    dest = str(tmp_path / "dup_dest")
    result = run_rehearsal(manifest=manifest, source_root=str(root), dest_root=dest)
    for obj in dup_objs:
        assert not any(
            r.get("source_path") == obj["source_path"] and r.get("written")
            for r in result.get("results", [])
        ), f"Duplicate basename object was written: {obj['source_path']}"


# ── Rehearsal engine: casefold collision ──────────────────────────────────────

def test_casefold_collision_rejected(tmp_path):
    root = tmp_path / "casefold_test"
    root.mkdir()
    (root / "Note.md").write_text("content A", encoding="utf-8")
    (root / "note.md").write_text("content B", encoding="utf-8")

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

    collision_objs = [o for o in manifest["objects"] if o["exception_code"] == EXC_CASEFOLD_COLLISION]
    assert len(collision_objs) >= 1, "Casefold collision not detected"

    dest = str(tmp_path / "casefold_dest")
    result = run_rehearsal(manifest=manifest, source_root=str(root), dest_root=dest)
    for obj in collision_objs:
        assert obj["action"] == ACTION_REJECT
        assert not any(
            r.get("source_path") == obj["source_path"] and r.get("written")
            for r in result.get("results", [])
        ), f"Casefold collision object was written: {obj['source_path']}"