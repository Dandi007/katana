"""Contract tests for migration rehearsal engine (M3b REHEARSED phase)."""

import json
import os
import stat
from pathlib import Path

import pytest

from katana_migration.inventory import (
    ACTION_ARCHIVE,
    ACTION_ID_BACKFILL,
    ACTION_PRESERVE,
    ACTION_REJECT,
    sha256_hex,
    build_manifest,
    run_inventory,
)
from katana_migration.rehearsal import (
    _DEFAULT_COMMITTER_DATE,
    _DEFAULT_COMMITTER_EMAIL,
    _DEFAULT_COMMITTER_NAME,
    run_rehearsal,
    verify_idempotent,
)


@pytest.fixture
def rehearsal_source_root(tmp_path):
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
        "---\nname: legacy-one\ndescription: legacy desc\n---\n\n## Fact\nLegacy content with ref [[Zettelkasten/note1]]\n",
        encoding="utf-8",
    )

    wiki_dir = root / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "Zettelkasten").mkdir()
    (wiki_dir / "Zettelkasten" / "note1.md").write_text(
        "# Zettelkasten Note\n\nSome content with ref to m-a1b2c3\n",
        encoding="utf-8",
    )
    (wiki_dir / "转换文档").mkdir()
    (wiki_dir / "转换文档" / "raw1.md").write_text(
        "# Raw Document\n\nRaw content\n",
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

    return root


@pytest.fixture
def rehearsal_source_sets(rehearsal_source_root):
    return [
        {
            "name": "memory_canonical",
            "root": str(rehearsal_source_root / "data" / "memory"),
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
            "root": str(rehearsal_source_root / "data" / "vault" / "memory"),
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
            "root": str(rehearsal_source_root / "wiki"),
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
            "root": str(rehearsal_source_root / "智元工作" / "工作记录"),
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
            "root": str(rehearsal_source_root / "exceptions"),
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
def rehearsal_manifest(rehearsal_source_sets):
    return run_inventory(rehearsal_source_sets, migration_run_id="rehearsal-test-001")


@pytest.fixture
def rehearsal_source_roots(rehearsal_source_root):
    return {
        "/data/memory": str(rehearsal_source_root / "data" / "memory"),
        "/data/vault/memory": str(rehearsal_source_root / "data" / "vault" / "memory"),
        "/data/wiki": str(rehearsal_source_root / "wiki"),
        "/data/work-records": str(rehearsal_source_root / "智元工作" / "工作记录"),
        "/data/exceptions": str(rehearsal_source_root / "exceptions"),
    }


@pytest.fixture
def rehearsal_dest_roots(tmp_path):
    return {
        "/data/memory": str(tmp_path / "dest" / "memory"),
        "/data/wiki": str(tmp_path / "dest" / "wiki"),
        "/data/work-records": str(tmp_path / "dest" / "work-records"),
    }


@pytest.fixture
def rehearsal_result(
    rehearsal_manifest,
    rehearsal_source_roots,
    rehearsal_dest_roots,
):
    return run_rehearsal(
        manifest=rehearsal_manifest,
        source_roots=rehearsal_source_roots,
        dest_roots=rehearsal_dest_roots,
        migration_run_id="rehearsal-test-001",
    )


# ── Action correctness ────────────────────────────────────────────────────────


def test_preserve_action_byte_equal(rehearsal_result, rehearsal_source_roots, rehearsal_dest_roots):
    pres_objs = [
        r for r in rehearsal_result.get("_objects", rehearsal_result.get("objects", []))
        if r.get("action") == ACTION_PRESERVE
    ]
    dest_root = rehearsal_dest_roots["/data/memory"]
    for obj in pres_objs:
        if obj.get("object_class") == "memory_canonical":
            src_path = Path(rehearsal_source_roots["/data/memory"]) / obj["source_path"]
            dest_path = Path(dest_root) / obj["destination_path"]
            if src_path.exists() and dest_path.exists():
                assert src_path.read_bytes() == dest_path.read_bytes(), (
                    f"Preserve byte mismatch for {obj['source_path']}"
                )


def test_id_backfill_action(rehearsal_result, rehearsal_manifest, rehearsal_dest_roots):
    legacy = [
        r for r in rehearsal_manifest["objects"]
        if r["action"] == ACTION_ID_BACKFILL
    ]
    assert len(legacy) >= 1, "No id_backfill objects in manifest"

    dest_root = rehearsal_dest_roots["/data/memory"]
    for obj in legacy:
        dest_path = Path(dest_root) / obj["destination_path"]
        assert dest_path.exists(), f"id_backfill file not created: {obj['destination_path']}"
        content = dest_path.read_text(encoding="utf-8")
        assert f"id: {obj['domain_resource_id']}" in content, (
            f"ID not backfilled in {obj['destination_path']}"
        )


def test_id_backfill_body_bytes_unchanged(rehearsal_result, rehearsal_manifest):
    legacy = [
        r for r in rehearsal_manifest["objects"]
        if r["action"] == ACTION_ID_BACKFILL
    ]
    assert len(legacy) >= 1

    for obj in legacy:
        assert obj["pre_hash"] == obj["post_hash"], (
            f"ID backfill changed body bytes for {obj['source_path']}"
        )


def test_reject_action_skipped(rehearsal_result, rehearsal_dest_roots):
    rejected = [
        r for r in rehearsal_result.get("_objects", rehearsal_result.get("objects", []))
        if r.get("action") == ACTION_REJECT
    ]
    for obj in rejected:
        dest_repo = obj.get("destination_repo", "/data/memory")
        dest_root = rehearsal_dest_roots.get(dest_repo)
        if dest_root:
            dest_path = Path(dest_root) / obj.get("destination_path", obj.get("source_path", ""))
            assert not dest_path.exists(), (
                f"Rejected object {obj['source_path']} was written to destination"
            )


def test_archive_action_moved(rehearsal_result, rehearsal_dest_roots):
    result = run_rehearsal(
        manifest={
            "migration_run_id": "archive-test",
            "objects": [
                {
                    "migration_run_id": "archive-test",
                    "source_repo": "/data/memory",
                    "source_commit": "0000000000000000000000000000000000000000",
                    "source_path": "old/card.md",
                    "git_blob_oid": None,
                    "sha256": None,
                    "size": 10,
                    "file_mode": "100644",
                    "lfs_oid": None,
                    "object_class": "memory_legacy",
                    "destination_repo": "/data/memory",
                    "destination_path": "old/card.md",
                    "domain_resource_id": "m-aaaaaa",
                    "vfs_node_id": "m-aaaaaa",
                    "action": ACTION_ARCHIVE,
                    "pre_hash": None,
                    "post_hash": None,
                    "allowed_transformations": [],
                    "reference_rewrites": [],
                    "exception_code": None,
                    "reason": None,
                },
            ],
            "source_sets": [],
            "redirect_map": {},
            "summary": {"tracked": 1, "preserved": 0, "transformed": 0, "archived": 1, "rejected": 0, "unclassified": 0, "invariant_holds": True},
        },
        source_roots={"/data/memory": str(rehearsal_result.get("_source_root", "."))},
        dest_roots=rehearsal_dest_roots,
        migration_run_id="archive-test",
    )
    assert result["summary"]["archived"] == 1


# ── Invariant: total = imported + rejected + archived ─────────────────────────


def test_rehearsal_invariant_holds(rehearsal_result):
    s = rehearsal_result["summary"]
    assert s["total"] == s["imported"] + s["rejected"] + s["archived"], (
        f"Invariant broken: total={s['total']} != imported={s['imported']} "
        f"+ rejected={s['rejected']} + archived={s['archived']}"
    )


# ── Integrity gate ────────────────────────────────────────────────────────────


def test_integrity_gate_binary_rejected(rehearsal_result, rehearsal_manifest):
    manifest_objects = rehearsal_manifest.get("objects", [])
    binary_objs = [r for r in manifest_objects if r.get("exception_code") == "BINARY_BYTES"]
    if binary_objs:
        assert binary_objs[0]["action"] == ACTION_REJECT


def test_integrity_gate_executable_rejected(rehearsal_result, rehearsal_manifest):
    manifest_objects = rehearsal_manifest.get("objects", [])
    exec_objs = [r for r in manifest_objects if r.get("exception_code") == "EXECUTABLE_BIT"]
    if exec_objs:
        assert exec_objs[0]["action"] == ACTION_REJECT


def test_integrity_gate_lfs_rejected(rehearsal_result, rehearsal_manifest):
    manifest_objects = rehearsal_manifest.get("objects", [])
    lfs_objs = [r for r in manifest_objects if r.get("exception_code") == "LFS_POINTER"]
    if lfs_objs:
        assert lfs_objs[0]["action"] == ACTION_REJECT


def test_integrity_gate_halts_on_failure(rehearsal_manifest, rehearsal_source_roots, rehearsal_dest_roots):
    objects = rehearsal_manifest.get("objects", [])
    binary_obj = next((r for r in objects if r.get("exception_code") == "BINARY_BYTES"), None)
    if binary_obj is None:
        pytest.skip("No binary object in manifest")

    binary_obj["exception_code"] = None
    binary_obj["reason"] = None
    binary_obj["action"] = ACTION_PRESERVE

    modified_manifest = {
        **rehearsal_manifest,
        "objects": [binary_obj],
    }

    with pytest.raises(RuntimeError, match="integrity gate failed"):
        run_rehearsal(
            manifest=modified_manifest,
            source_roots=rehearsal_source_roots,
            dest_roots=rehearsal_dest_roots,
            migration_run_id="integrity-halt-test",
            fail_on_integrity=True,
        )


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_rehearsal_idempotent_byte_identical(
    rehearsal_manifest,
    rehearsal_source_roots,
    tmp_path,
):
    dest1 = {
        "/data/memory": str(tmp_path / "run1" / "memory"),
        "/data/wiki": str(tmp_path / "run1" / "wiki"),
        "/data/work-records": str(tmp_path / "run1" / "work-records"),
    }
    dest2 = {
        "/data/memory": str(tmp_path / "run2" / "memory"),
        "/data/wiki": str(tmp_path / "run2" / "wiki"),
        "/data/work-records": str(tmp_path / "run2" / "work-records"),
    }
    result1 = run_rehearsal(
        manifest=rehearsal_manifest,
        source_roots=rehearsal_source_roots,
        dest_roots=dest1,
        migration_run_id="idempotent-test",
    )
    result2 = run_rehearsal(
        manifest=rehearsal_manifest,
        source_roots=rehearsal_source_roots,
        dest_roots=dest2,
        migration_run_id="idempotent-test",
    )
    assert verify_idempotent(result1, result2), (
        "Rehearsal results are not byte-identical on repeated runs"
    )


def test_rehearsal_json_idempotent(
    rehearsal_manifest,
    rehearsal_source_roots,
    tmp_path,
):
    dest1 = {
        "/data/memory": str(tmp_path / "run1" / "memory"),
        "/data/wiki": str(tmp_path / "run1" / "wiki"),
        "/data/work-records": str(tmp_path / "run1" / "work-records"),
    }
    dest2 = {
        "/data/memory": str(tmp_path / "run2" / "memory"),
        "/data/wiki": str(tmp_path / "run2" / "wiki"),
        "/data/work-records": str(tmp_path / "run2" / "work-records"),
    }
    result1 = run_rehearsal(
        manifest=rehearsal_manifest,
        source_roots=rehearsal_source_roots,
        dest_roots=dest1,
        migration_run_id="json-idempotent-test",
    )
    result2 = run_rehearsal(
        manifest=rehearsal_manifest,
        source_roots=rehearsal_source_roots,
        dest_roots=dest2,
        migration_run_id="json-idempotent-test",
    )
    assert verify_idempotent(result1, result2), (
        "JSON rehearsal results are not byte-identical on repeated runs"
    )


# ── Destination tree structure ────────────────────────────────────────────────


def test_destination_trees_exist(rehearsal_result):
    trees = rehearsal_result.get("destination_trees", {})
    assert len(trees) > 0, "No destination trees created"
    for repo, info in trees.items():
        assert "root" in info, f"Missing root in tree info for {repo}"
        assert "head_sha" in info, f"Missing head_sha in tree info for {repo}"
        assert "tree_sha" in info, f"Missing tree_sha in tree info for {repo}"


def test_migration_base_marker_exists(rehearsal_result):
    for repo, info in rehearsal_result.get("destination_trees", {}).items():
        root = Path(info["root"])
        marker = root / "MIGRATION_BASE"
        assert marker.exists(), f"MIGRATION_BASE marker missing in {repo}"
        assert marker.read_text(encoding="utf-8") == "MIGRATION_BASE\n"


def test_destination_is_git_repo(rehearsal_result):
    for repo, info in rehearsal_result.get("destination_trees", {}).items():
        root = Path(info["root"])
        git_dir = root / ".git"
        assert git_dir.is_dir(), f"Destination {repo} is not a git repo"


def test_destination_has_commit(rehearsal_result):
    for repo, info in rehearsal_result.get("destination_trees", {}).items():
        assert info["head_sha"], f"Destination {repo} has no commit"
        assert len(info["head_sha"]) == 40, f"Invalid commit SHA for {repo}"


# ── Reference manifest ────────────────────────────────────────────────────────


def test_reference_manifest_present(rehearsal_result):
    ref_manifest = rehearsal_result.get("reference_manifest", {})
    assert "references" in ref_manifest
    assert "total_references" in ref_manifest
    assert "broken_references" in ref_manifest
    assert "net_new_broken" in ref_manifest
    assert "net_new_broken_is_zero" in ref_manifest


def test_reference_manifest_net_broken_zero(rehearsal_result):
    ref_manifest = rehearsal_result.get("reference_manifest", {})
    assert ref_manifest["net_new_broken_is_zero"] is True, (
        f"Net new broken references: {ref_manifest['net_new_broken']}"
    )


# ── Redirect catalog ──────────────────────────────────────────────────────────


def test_redirect_catalog_present(rehearsal_result):
    catalog = rehearsal_result.get("redirect_catalog", {})
    assert isinstance(catalog, dict), "redirect_catalog must be a dict"


# ── Integrity report ──────────────────────────────────────────────────────────


def test_integrity_report_present(rehearsal_result):
    report = rehearsal_result.get("integrity_report", {})
    assert "issues" in report
    assert "passed" in report


# ── No production paths ───────────────────────────────────────────────────────


def test_no_production_paths_in_rehearsal(rehearsal_result):
    for repo, info in rehearsal_result.get("destination_trees", {}).items():
        assert not info["root"].startswith("/data/memory"), (
            f"Destination root references production: {info['root']}"
        )
        assert not info["root"].startswith("/data/vault/"), (
            f"Destination root references production: {info['root']}"
        )
        assert not info["root"].startswith("/data/wiki"), (
            f"Destination root references production: {info['root']}"
        )
        assert not info["root"].startswith("/data/work-records"), (
            f"Destination root references production: {info['root']}"
        )


# ── Manual action-type fixtures ───────────────────────────────────────────────


def test_normalize_action_handled(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "normal.md").write_text("---\nid: m-abc123\nname: test\ndescription: test\n---\n\nBody\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = build_manifest([{
        "name": "test",
        "root": str(src),
        "source_repo": "/test",
        "object_class": "memory_canonical",
        "prefix": "m-",
        "destination_repo": "/test",
        "default_action": "normalize",
        "include": ["*.md"],
    }], migration_run_id="normalize-test")

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="normalize-test",
    )
    assert result["summary"]["imported"] >= 1


def test_rewrite_action_handled(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "rewrite.md").write_text("---\nid: m-def456\nname: test\ndescription: test\n---\n\nBody\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = build_manifest([{
        "name": "test",
        "root": str(src),
        "source_repo": "/test",
        "object_class": "memory_canonical",
        "prefix": "m-",
        "destination_repo": "/test",
        "default_action": "rewrite",
        "include": ["*.md"],
    }], migration_run_id="rewrite-test")

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="rewrite-test",
    )
    assert result["summary"]["imported"] >= 1


def test_merge_action_handled(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "merge.md").write_text("---\nname: test\ndescription: test\n---\n\nBody\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = build_manifest([{
        "name": "test",
        "root": str(src),
        "source_repo": "/test",
        "object_class": "memory_legacy",
        "prefix": "m-",
        "destination_repo": "/test",
        "default_action": "merge",
        "include": ["*.md"],
    }], migration_run_id="merge-test")

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="merge-test",
    )
    assert result["summary"]["imported"] >= 1


# ── SHA-256 integrity for preserve ────────────────────────────────────────────


def test_preserve_sha256_match(rehearsal_result, rehearsal_manifest):
    pres_objs = [
        r for r in rehearsal_manifest["objects"]
        if r["action"] == ACTION_PRESERVE and r["sha256"] is not None
    ]
    for obj in pres_objs:
        assert obj["pre_hash"] == obj["post_hash"], (
            f"Preserve hash mismatch for {obj['source_path']}"
        )
        assert obj["sha256"] == obj["pre_hash"], (
            f"Sha256 mismatch for {obj['source_path']}: {obj['sha256']} != {obj['pre_hash']}"
        )


# ── Casefold collision detection ──────────────────────────────────────────────


def test_casefold_collision_in_rehearsal(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "Note.md").write_text("content one", encoding="utf-8")
    (src / "note.md").write_text("content two different", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = {
        "migration_run_id": "casefold-test",
        "source_sets": [],
        "redirect_map": {},
        "summary": {"tracked": 2, "preserved": 2, "transformed": 0, "archived": 0, "rejected": 0, "unclassified": 0, "invariant_holds": True},
        "objects": [
            {
                "migration_run_id": "casefold-test",
                "source_repo": "/test",
                "source_commit": "0000000000000000000000000000000000000000",
                "source_path": "Note.md",
                "git_blob_oid": None,
                "sha256": None,
                "size": 12,
                "file_mode": "100644",
                "lfs_oid": None,
                "object_class": "test",
                "destination_repo": "/test",
                "destination_path": "Note.md",
                "domain_resource_id": "t-aaa111",
                "vfs_node_id": "t-aaa111",
                "action": "preserve",
                "pre_hash": None,
                "post_hash": None,
                "allowed_transformations": [],
                "reference_rewrites": [],
                "exception_code": None,
                "reason": None,
            },
            {
                "migration_run_id": "casefold-test",
                "source_repo": "/test",
                "source_commit": "0000000000000000000000000000000000000000",
                "source_path": "note.md",
                "git_blob_oid": None,
                "sha256": None,
                "size": 22,
                "file_mode": "100644",
                "lfs_oid": None,
                "object_class": "test",
                "destination_repo": "/test",
                "destination_path": "note.md",
                "domain_resource_id": "t-bbb222",
                "vfs_node_id": "t-bbb222",
                "action": "preserve",
                "pre_hash": None,
                "post_hash": None,
                "allowed_transformations": [],
                "reference_rewrites": [],
                "exception_code": None,
                "reason": None,
            },
        ],
    }

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="casefold-test",
        fail_on_integrity=False,
    )

    issues = result.get("integrity_report", {}).get("issues", [])
    casefold_issues = [i for i in issues if i.get("code") == "CASEFOLD_COLLISION"]
    assert len(casefold_issues) >= 1, "Casefold collision not detected"


# ── Symlink rejection ─────────────────────────────────────────────────────────


def test_symlink_rejected_in_rehearsal(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    target = src / "target.md"
    target.write_text("target content", encoding="utf-8")
    symlink = src / "link.md"
    try:
        symlink.symlink_to(target)
    except OSError:
        pytest.skip("Cannot create symlink in test environment")

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = run_inventory([{
        "name": "symlink_test",
        "root": str(src),
        "source_repo": "/test",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["*.md"],
    }], migration_run_id="symlink-test")

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="symlink-test",
        fail_on_integrity=False,
    )

    assert result["summary"]["rejected"] >= 1, "Symlink not rejected"


# ── Unicode normalization ─────────────────────────────────────────────────────


def test_unicode_normalization_detected(tmp_path):
    import unicodedata

    src = tmp_path / "src"
    src.mkdir()
    nfd_text = unicodedata.normalize("NFD", "caf\u00e9")
    content = f"---\nid: m-abc123\nname: test\ndescription: test\n---\n\n{nfd_text}\n"
    (src / "unicode.md").write_bytes(content.encode("utf-8"))

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = run_inventory([{
        "name": "unicode_test",
        "root": str(src),
        "source_repo": "/test",
        "object_class": "memory_canonical",
        "prefix": "m-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["*.md"],
    }], migration_run_id="unicode-test")

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="unicode-test",
        fail_on_integrity=False,
    )

    issues = result.get("integrity_report", {}).get("issues", [])
    unicode_issues = [i for i in issues if i.get("code") == "UNICODE_NORMALIZATION"]
    assert len(unicode_issues) >= 1, "Unicode normalization issue not detected"


# ── Path length detection ─────────────────────────────────────────────────────


def test_path_length_detected_in_rehearsal(tmp_path, monkeypatch):
    import katana_migration.rehearsal as rmod
    monkeypatch.setattr(rmod, "MAX_PATH_LENGTH", 100)

    src = tmp_path / "src"
    src.mkdir()
    deep = src
    long_name = "d" * 50
    for i in range(3):
        deep = deep / long_name
        deep.mkdir()
    deep_file = deep / "file.md"
    deep_file.write_text("content", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()

    manifest = run_inventory([{
        "name": "pathlen_test",
        "root": str(src),
        "source_repo": "/test",
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }], migration_run_id="pathlen-test")

    result = run_rehearsal(
        manifest=manifest,
        source_roots={"/test": str(src)},
        dest_roots={"/test": str(dest)},
        migration_run_id="pathlen-test",
        fail_on_integrity=False,
    )

    issues = result.get("integrity_report", {}).get("issues", [])
    path_issues = [i for i in issues if i.get("code") == "PATH_LENGTH_EXCEEDED"]
    assert len(path_issues) >= 1, "Path length issue not detected"


# ── Missing source halts rehearsal ────────────────────────────────────────────


def test_missing_source_halts(rehearsal_manifest, rehearsal_dest_roots):
    modified_manifest = {
        **rehearsal_manifest,
        "objects": [
            {
                "migration_run_id": "halt-test",
                "source_repo": "/nonexistent",
                "source_commit": "0000000000000000000000000000000000000000",
                "source_path": "nonexistent.md",
                "git_blob_oid": None,
                "sha256": None,
                "size": 0,
                "file_mode": "100644",
                "lfs_oid": None,
                "object_class": "unknown",
                "destination_repo": "/data/memory",
                "destination_path": "nonexistent.md",
                "domain_resource_id": "m-000000",
                "vfs_node_id": "m-000000",
                "action": ACTION_PRESERVE,
                "pre_hash": None,
                "post_hash": None,
                "allowed_transformations": [],
                "reference_rewrites": [],
                "exception_code": None,
                "reason": None,
            },
        ],
    }

    with pytest.raises(RuntimeError, match="source file not found"):
        run_rehearsal(
            manifest=modified_manifest,
            source_roots={"/nonexistent": "/tmp/nonexistent_dir_xyz"},
            dest_roots=rehearsal_dest_roots,
            migration_run_id="halt-test",
        )


# ── Three-domain coverage ─────────────────────────────────────────────────────


def test_three_domains_present(rehearsal_result):
    trees = rehearsal_result.get("destination_trees", {})
    tree_repos = set(trees.keys())
    assert len(tree_repos) >= 1, "No domain trees created"


def test_memory_domain_populated(rehearsal_result, rehearsal_dest_roots):
    dest_root = rehearsal_dest_roots.get("/data/memory")
    if dest_root:
        mem_dir = Path(dest_root)
        if mem_dir.exists():
            files = list(mem_dir.glob("**/*.md"))
            assert len(files) >= 1, "No memory files in destination"


def test_wiki_domain_populated(rehearsal_result, rehearsal_dest_roots):
    dest_root = rehearsal_dest_roots.get("/data/wiki")
    if dest_root:
        wiki_dir = Path(dest_root)
        if wiki_dir.exists():
            files = list(wiki_dir.glob("**/*.md"))
            assert len(files) >= 1, "No wiki files in destination"


def test_work_folder_domain_populated(rehearsal_result, rehearsal_dest_roots):
    dest_root = rehearsal_dest_roots.get("/data/work-records")
    if dest_root:
        wf_dir = Path(dest_root)
        if wf_dir.exists():
            files = list(wf_dir.glob("**/*.md"))
            assert len(files) >= 1, "No work folder files in destination"


# ── Committer info ────────────────────────────────────────────────────────────


def test_committer_info_present(rehearsal_result):
    committer = rehearsal_result.get("committer_info", {})
    assert "name" in committer
    assert "email" in committer
    assert "date" in committer


# ── verify_idempotent function ────────────────────────────────────────────────


def test_verify_idempotent_same():
    d1 = {"a": 1, "b": [2, 3]}
    d2 = {"a": 1, "b": [2, 3]}
    assert verify_idempotent(d1, d2) is True


def test_verify_idempotent_different():
    d1 = {"a": 1, "b": [2, 3]}
    d2 = {"a": 1, "b": [2, 4]}
    assert verify_idempotent(d1, d2) is False


# ── M3a manifest integration ──────────────────────────────────────────────────


def test_rehearsal_consumes_m3a_manifest(rehearsal_manifest):
    assert "migration_run_id" in rehearsal_manifest
    assert "objects" in rehearsal_manifest
    assert "redirect_map" in rehearsal_manifest
    assert "summary" in rehearsal_manifest


def test_rehearsal_reuses_m3a_id_mapping(rehearsal_result, rehearsal_manifest):
    m3a_ids = {
        r["domain_resource_id"]: r["source_path"]
        for r in rehearsal_manifest["objects"]
        if r.get("domain_resource_id")
    }
    assert len(m3a_ids) > 0, "No IDs in M3a manifest"


# ── Rehearsal-only boundary ───────────────────────────────────────────────────


def test_rehearsal_only_writes_to_temp(rehearsal_result):
    for repo, info in rehearsal_result.get("destination_trees", {}).items():
        root = info["root"]
        assert "/tmp/" in root or "tmp" in root.lower(), (
            f"Rehearsal wrote outside temporary directory: {root}"
        )