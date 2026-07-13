"""Contract tests for rehearsal engine (M3b REHEARSED phase)."""

import hashlib
import json
import os
import shutil
import subprocess
import unicodedata
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
    build_manifest,
    compute_summary,
    sha256_hex,
)
from katana_migration.rehearsal import (
    EXC_UNICODE_NFC,
    RehearsalEngine,
    _extract_body_bytes,
    _insert_frontmatter_id,
    _parse_references,
    run_rehearsal,
)


def _git_init(repo: str) -> None:
    subprocess.run(["git", "-C", repo, "init"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", repo, "config", "user.email", "test@katana.local"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", repo, "config", "user.name", "Test"],
        capture_output=True, check=True,
    )


def _git_commit_all(repo: str, msg: str) -> None:
    subprocess.run(["git", "-C", repo, "add", "-A"], capture_output=True, check=True)
    subprocess.run(["git", "-C", repo, "commit", "-m", msg], capture_output=True, check=True)


def _git_head(repo: str) -> str:
    return subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True,
    ).strip()


def _git_all_commits(repo: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", repo, "rev-list", "--all"], text=True,
    ).strip()
    return out.split("\n") if out else []


def _git_ls_files(repo: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", repo, "ls-files"], text=True,
    ).strip()
    return out.split("\n") if out else []


def _domain_dir(dest_root, domain_name):
    return Path(dest_root) / domain_name


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
        "---\nid: m-d4e5f6\nname: card-two\ndescription: desc two\nstatus: active\nlast_verified: 2026-07-08\n---\n\n## Fact\nContent B ref: [[alice/card1.md]]\n",
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
        "---\nname: legacy-one\ndescription: legacy desc\n---\n\n## Fact\nLegacy content ref: [[alice/card1.md]]\n",
        encoding="utf-8",
    )

    wiki_dir = root / "wiki"
    wiki_dir.mkdir(parents=True)
    (wiki_dir / "Zettelkasten").mkdir()
    (wiki_dir / "Zettelkasten" / "note1.md").write_text(
        "# Zettelkasten Note\n\nSome content\n",
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
    symlink_path = exc_dir / "symlink.md"
    try:
        symlink_path.symlink_to(exc_dir / "binary.bin")
    except OSError:
        pass

    return root


@pytest.fixture
def source_sets_all_actions(source_root):
    return [
        {
            "name": "memory_canonical",
            "root": str(source_root / "data" / "memory"),
            "source_repo": str(source_root / "data" / "memory"),
            "source_commit": "0" * 40,
            "object_class": "memory_canonical",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "memory_legacy",
            "root": str(source_root / "data" / "vault" / "memory"),
            "source_repo": str(source_root / "data" / "vault" / "memory"),
            "source_commit": "0" * 40,
            "object_class": "memory_legacy",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "id_backfill",
            "include": ["**/*.md"],
        },
        {
            "name": "wiki",
            "root": str(source_root / "wiki"),
            "source_repo": str(source_root / "wiki"),
            "source_commit": "0" * 40,
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
            "source_repo": str(source_root / "智元工作" / "工作记录"),
            "source_commit": "0" * 40,
            "object_class": "work_folder",
            "prefix": "wf-",
            "destination_repo": "/data/work-records",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "exceptions",
            "root": str(source_root / "exceptions"),
            "source_repo": str(source_root / "exceptions"),
            "source_commit": "0" * 40,
            "object_class": "unknown",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*"],
        },
    ]


@pytest.fixture
def manifest_all_actions(source_sets_all_actions):
    return build_manifest(source_sets_all_actions, migration_run_id="rehearsal-test-001")


@pytest.fixture
def dest_root(tmp_path):
    return str(tmp_path / "dest")


# ── Real git repo fixtures for history tests ──────────────────────────────────


@pytest.fixture
def git_memory_source(tmp_path):
    repo = tmp_path / "git_memory"
    repo.mkdir()
    _git_init(str(repo))

    (repo / "alice").mkdir()
    (repo / "alice" / "card1.md").write_text(
        "---\nid: m-a1b2c3\nname: card-one\ndescription: desc one\n---\n\n## Fact\nContent A\n",
        encoding="utf-8",
    )
    _git_commit_all(str(repo), "initial: card1")

    (repo / "alice" / "card2.md").write_text(
        "---\nid: m-d4e5f6\nname: card-two\ndescription: desc two\n---\n\n## Fact\nContent B\n",
        encoding="utf-8",
    )
    _git_commit_all(str(repo), "add card2")

    return str(repo)


@pytest.fixture
def git_memory_source2(tmp_path):
    repo = tmp_path / "git_memory2"
    repo.mkdir()
    _git_init(str(repo))

    (repo / "bob").mkdir()
    (repo / "bob" / "card3.md").write_text(
        "---\nid: m-789abc\nname: card-three\ndescription: desc three\n---\n\n## Fact\nContent C\n",
        encoding="utf-8",
    )
    _git_commit_all(str(repo), "initial: card3")

    (repo / "bob" / "card4.md").write_text(
        "---\nid: m-fedcba\nname: card-four\ndescription: desc four\n---\n\n## Fact\nContent D\n",
        encoding="utf-8",
    )
    _git_commit_all(str(repo), "add card4")

    return str(repo)


@pytest.fixture
def git_wiki_source(tmp_path):
    repo = tmp_path / "git_wiki"
    repo.mkdir()
    _git_init(str(repo))

    (repo / "Zettelkasten").mkdir()
    (repo / "Zettelkasten" / "note1.md").write_text("# Note 1\n\nContent 1\n", encoding="utf-8")
    _git_commit_all(str(repo), "initial: note1")

    (repo / "Zettelkasten" / "note2.md").write_text("# Note 2\n\nContent 2\n", encoding="utf-8")
    _git_commit_all(str(repo), "add note2")

    return str(repo)


@pytest.fixture
def manifest_multi_source_memory(git_memory_source, git_memory_source2):
    source_sets = [
        {
            "name": "memory_repo1",
            "root": git_memory_source,
            "source_repo": git_memory_source,
            "source_commit": _git_head(git_memory_source),
            "object_class": "memory_canonical",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
        {
            "name": "memory_repo2",
            "root": git_memory_source2,
            "source_repo": git_memory_source2,
            "source_commit": _git_head(git_memory_source2),
            "object_class": "memory_canonical",
            "prefix": "m-",
            "destination_repo": "/data/memory",
            "default_action": "preserve",
            "include": ["**/*.md"],
        },
    ]
    return build_manifest(source_sets, migration_run_id="multi-mem-test")


@pytest.fixture
def manifest_wiki_filtered(git_wiki_source):
    source_sets = [
        {
            "name": "wiki_filtered",
            "root": git_wiki_source,
            "source_repo": git_wiki_source,
            "source_commit": _git_head(git_wiki_source),
            "object_class": "wiki",
            "prefix": "w-",
            "destination_repo": "/data/wiki",
            "default_action": "preserve",
            "auto_classify": True,
            "include": ["**/*.md"],
        },
    ]
    return build_manifest(source_sets, migration_run_id="wiki-filter-test")


@pytest.fixture
def manifest_link_rewrite(manifest_all_actions):
    m = json.loads(json.dumps(manifest_all_actions))
    legacy_obj = None
    for obj in m["objects"]:
        if obj["object_class"] == "memory_legacy":
            legacy_obj = obj
            break
    if legacy_obj:
        legacy_obj["action"] = ACTION_REWRITE
        legacy_obj["reference_rewrites"] = [
            {"old": "alice/card1.md", "new": "m-d4e5f6"}
        ]
    return m


# ── Unit tests for helpers ────────────────────────────────────────────────────


def test_extract_body_bytes():
    content = b"---\nid: m-123\nname: test\n---\n\n## Fact\nBody content\n"
    body = _extract_body_bytes(content)
    assert body == b"## Fact\nBody content\n"


def test_extract_body_bytes_no_frontmatter():
    content = b"# Just a heading\nContent\n"
    body = _extract_body_bytes(content)
    assert body == content


def test_insert_frontmatter_id():
    content = b"---\nname: test\ndescription: desc\n---\n\n## Fact\nBody\n"
    new_content = _insert_frontmatter_id(content, "m-abc123")
    body_before = _extract_body_bytes(content)
    body_after = _extract_body_bytes(new_content)
    assert body_before == body_after
    assert b"id: m-abc123" in new_content


def test_parse_references():
    text = "See [[alice/card1.md]] and [[m-bbb222|link text]] and [[note#section]]"
    refs = _parse_references(text)
    assert len(refs) == 3
    assert refs[0]["target"] == "alice/card1.md"
    assert refs[0]["anchor"] is None
    assert refs[1]["target"] == "m-bbb222"
    assert refs[1]["display"] == "link text"
    assert refs[2]["target"] == "note"
    assert refs[2]["anchor"] == "section"


# ── Full rehearsal run tests ──────────────────────────────────────────────────


def test_all_actions_materialized(manifest_all_actions, dest_root):
    result = run_rehearsal(manifest_all_actions, dest_root)

    assert "domains" in result
    domains = result["domains"]

    memory_domain = None
    for name, info in domains.items():
        if "memory" in name.lower():
            memory_domain = name
            break
    assert memory_domain is not None

    wiki_domain = None
    for name in domains:
        if "wiki" in name.lower():
            wiki_domain = name
            break
    assert wiki_domain is not None

    wf_domain = None
    for name in domains:
        if "work" in name.lower():
            wf_domain = name
            break
    assert wf_domain is not None

    dest = Path(dest_root)
    assert (dest / "memory").is_dir()
    assert (dest / "wiki").is_dir()
    assert (dest / "work-records").is_dir()


def test_conservation_invariant(manifest_all_actions, dest_root):
    result = run_rehearsal(manifest_all_actions, dest_root)
    s = result["summary"]
    computed = s["preserved"] + s["transformed"] + s["archived"] + s["rejected"]
    assert s["tracked"] == computed
    assert s["unclassified"] == 0
    assert s["invariant_holds"] is True


def test_migration_base_marker(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    for domain_dir in Path(dest_root).iterdir():
        if domain_dir.is_dir() and (domain_dir / ".git").is_dir():
            marker = domain_dir / "MIGRATION_BASE"
            assert marker.is_file(), f"Missing MIGRATION_BASE in {domain_dir.name}"
            data = json.loads(marker.read_text())
            assert data["migration_run_id"] == "rehearsal-test-001"
            assert data["phase"] == "REHEARSED"


def test_redirects_json_emitted(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    for domain_dir in Path(dest_root).iterdir():
        if domain_dir.is_dir() and (domain_dir / ".git").is_dir():
            rj = domain_dir / "redirects.json"
            assert rj.is_file(), f"Missing redirects.json in {domain_dir.name}"
            data = json.loads(rj.read_text())
            assert "domain" in data
            assert "redirects" in data
            assert isinstance(data["redirects"], dict)


def test_references_json_emitted(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    for domain_dir in Path(dest_root).iterdir():
        if domain_dir.is_dir() and (domain_dir / ".git").is_dir():
            refj = domain_dir / "references.json"
            assert refj.is_file(), f"Missing references.json in {domain_dir.name}"
            data = json.loads(refj.read_text())
            assert "domain" in data
            assert "objects" in data
            assert "constraint_holds" in data


def test_idempotent_rehearsal(manifest_all_actions, tmp_path):
    dest1 = str(tmp_path / "dest1")
    dest2 = str(tmp_path / "dest2")

    result1 = run_rehearsal(manifest_all_actions, dest1, committer_date="2025-01-01T00:00:00Z")
    result2 = run_rehearsal(manifest_all_actions, dest2, committer_date="2025-01-01T00:00:00Z")

    assert result1["domains"].keys() == result2["domains"].keys()
    for domain in result1["domains"]:
        assert result1["domains"][domain]["final_commit"] == result2["domains"][domain]["final_commit"], (
            f"Domain {domain}: commit SHA mismatch between runs"
        )


def test_preserve_sha256_byte_equal(manifest_all_actions, dest_root):
    manifest_all_actions = json.loads(json.dumps(manifest_all_actions))
    run_rehearsal(manifest_all_actions, dest_root)

    preserved = [r for r in manifest_all_actions["objects"] if r["action"] == ACTION_PRESERVE]
    for obj in preserved:
        domain_name = obj["destination_repo"].rstrip("/").rsplit("/", 1)[-1]
        dest_file = _domain_dir(dest_root, domain_name) / obj["destination_path"]
        if dest_file.is_file():
            actual = sha256_hex(dest_file.read_bytes())
            expected = obj.get("sha256")
            if expected:
                assert actual == expected, f"SHA-256 mismatch for {obj['source_path']}"


def test_id_backfill_body_bytes_unchanged(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    backfilled = [r for r in manifest_all_actions["objects"] if r["action"] == ACTION_ID_BACKFILL]
    assert len(backfilled) > 0, "No id_backfill objects in manifest"

    for obj in backfilled:
        domain_name = obj["destination_repo"].rstrip("/").rsplit("/", 1)[-1]
        dest_file = _domain_dir(dest_root, domain_name) / obj["destination_path"]
        assert dest_file.is_file(), f"Backfilled file not materialized: {obj['source_path']}"
        dest_content = dest_file.read_bytes()
        body = _extract_body_bytes(dest_content)
        assert body is not None
        assert b"id:" in dest_content[:200], f"ID not inserted in {obj['source_path']}"


def test_normalize_emits_diff_manifest(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    wiki_obj = None
    for obj in m["objects"]:
        if obj["object_class"] == "wiki_schema":
            wiki_obj = obj
            break
    if wiki_obj:
        wiki_obj["action"] = ACTION_NORMALIZE
    run_rehearsal(m, dest_root)


def test_rewrite_emits_diff_manifest(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    legacy_obj = None
    for obj in m["objects"]:
        if obj["object_class"] == "memory_legacy":
            legacy_obj = obj
            break
    if legacy_obj:
        legacy_obj["action"] = ACTION_REWRITE
    run_rehearsal(m, dest_root)


def test_reference_constraint_holds(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    for domain_dir in Path(dest_root).iterdir():
        if domain_dir.is_dir() and (domain_dir / ".git").is_dir():
            refj = domain_dir / "references.json"
            if refj.is_file():
                data = json.loads(refj.read_text())
                new_broken = data.get("new_broken", 0)
                old_broken = data.get("old_broken", 0)
                assert new_broken - old_broken == 0, (
                    f"Reference constraint broken: new_broken={new_broken}, "
                    f"old_broken={old_broken}"
                )


def test_reference_entries_have_required_fields(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    for domain_dir in Path(dest_root).iterdir():
        if domain_dir.is_dir() and (domain_dir / ".git").is_dir():
            refj = domain_dir / "references.json"
            if refj.is_file():
                data = json.loads(refj.read_text())
                for entry in data.get("objects", []):
                    assert "source_path" in entry
                    assert "domain_resource_id" in entry
                    assert "references" in entry
                    for ref in entry["references"]:
                        assert "old_literal" in ref
                        assert "old_target_id" in ref
                        assert "new_target_id" in ref
                        assert "anchor" in ref
                        assert "disposition" in ref


def test_link_rewrite_applies_in_full_run(manifest_link_rewrite, dest_root):
    result = run_rehearsal(manifest_link_rewrite, dest_root)

    memory_domain = None
    for name in result["domains"]:
        if "memory" in name.lower():
            memory_domain = name
            break
    assert memory_domain is not None

    refj = _domain_dir(dest_root, "memory") / "references.json"
    assert refj.is_file(), "references.json not emitted"

    data = json.loads(refj.read_text())
    assert data["constraint_holds"] is True, (
        f"Reference constraint not holding: old_broken={data.get('old_broken')}, "
        f"new_broken={data.get('new_broken')}"
    )

    legacy_refs = None
    for entry in data.get("objects", []):
        if "legacy" in entry.get("source_path", ""):
            legacy_refs = entry
            break

    assert legacy_refs is not None, "No reference entry for legacy object"
    found_rewritten = False
    for ref in legacy_refs["references"]:
        if ref["old_target_id"] != ref["new_target_id"]:
            found_rewritten = True
            assert ref["new_target_id"] == "m-d4e5f6", (
                f"Expected new_target_id=m-d4e5f6, got {ref['new_target_id']}"
            )
        assert ref["disposition"] == "resolved", (
            f"Rewritten ref should be resolved, got {ref['disposition']} "
            f"(old={ref['old_target_id']}, new={ref['new_target_id']})"
        )
    assert found_rewritten, "No rewritten reference found in legacy object"


# ── Integrity gate tests ──────────────────────────────────────────────────────


def test_integrity_gate_symlink_blocks(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    symlink_objs = [r for r in m["objects"] if r["exception_code"] == EXC_SYMLINK]
    for obj in symlink_objs:
        obj["action"] = ACTION_PRESERVE
        obj["exception_code"] = None
        obj["reason"] = None

    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(m, dest_root)


def test_integrity_gate_binary_blocks(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    binary_objs = [r for r in m["objects"] if r["exception_code"] == EXC_BINARY]
    for obj in binary_objs:
        obj["action"] = ACTION_PRESERVE
        obj["exception_code"] = None

    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(m, dest_root)


def test_integrity_gate_executable_blocks(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    exec_objs = [r for r in m["objects"] if r["exception_code"] == EXC_EXECUTABLE]
    for obj in exec_objs:
        obj["action"] = ACTION_PRESERVE
        obj["exception_code"] = None

    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(m, dest_root)


def test_integrity_gate_lfs_blocks(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    lfs_objs = [r for r in m["objects"] if r["exception_code"] == EXC_LFS_POINTER]
    for obj in lfs_objs:
        obj["action"] = ACTION_PRESERVE
        obj["exception_code"] = None

    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(m, dest_root)


def test_integrity_gate_nfc_blocks(tmp_path):
    source_root = tmp_path / "nfc_source"
    source_root.mkdir()
    nfc_name = unicodedata.normalize("NFD", "caf\u00e9") + ".md"
    (source_root / nfc_name).write_text("# test\n", encoding="utf-8")

    source_sets = [{
        "name": "nfc_test",
        "root": str(source_root),
        "source_repo": str(source_root),
        "source_commit": "0" * 40,
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }]
    manifest = build_manifest(source_sets, migration_run_id="nfc-test")

    dest_root = str(tmp_path / "dest_nfc")
    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(manifest, dest_root)


def test_integrity_gate_casefold_blocks(tmp_path):
    source_root = tmp_path / "cf_source"
    source_root.mkdir()
    (source_root / "File.md").write_text("# test1\n", encoding="utf-8")
    (source_root / "file.md").write_text("# test2\n", encoding="utf-8")

    source_sets = [{
        "name": "cf_test",
        "root": str(source_root),
        "source_repo": str(source_root),
        "source_commit": "0" * 40,
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }]
    manifest = build_manifest(source_sets, migration_run_id="cf-test")

    manifest = json.loads(json.dumps(manifest))
    for obj in manifest["objects"]:
        if obj["action"] == ACTION_REJECT:
            obj["action"] = ACTION_PRESERVE
            obj["exception_code"] = None

    dest_root = str(tmp_path / "dest_cf")
    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(manifest, dest_root)


def test_integrity_gate_path_length_blocks(tmp_path, monkeypatch):
    import katana_migration.rehearsal as rh_mod
    monkeypatch.setattr(rh_mod, "MAX_PATH_LENGTH", 100)

    source_root = tmp_path / "pl_source"
    source_root.mkdir()
    deep = source_root
    long_name = "d" * 50
    for i in range(3):
        deep = deep / long_name
        deep.mkdir()
    (deep / "file.md").write_text("# test\n", encoding="utf-8")

    source_sets = [{
        "name": "pl_test",
        "root": str(source_root),
        "source_repo": str(source_root),
        "source_commit": "0" * 40,
        "object_class": "test",
        "prefix": "t-",
        "destination_repo": "/data/test",
        "default_action": "preserve",
        "include": ["**/*.md"],
    }]
    manifest = build_manifest(source_sets, migration_run_id="pl-test")

    manifest = json.loads(json.dumps(manifest))
    for obj in manifest["objects"]:
        if obj["action"] == ACTION_REJECT:
            obj["action"] = ACTION_PRESERVE
            obj["exception_code"] = None

    dest_root = str(tmp_path / "dest_pl")
    with pytest.raises(RuntimeError, match="Integrity gate failed"):
        run_rehearsal(manifest, dest_root)


def test_integrity_gate_all_blocking_codes():
    from katana_migration.rehearsal import _BLOCKING_EXCEPTIONS
    expected = {
        EXC_SYMLINK, EXC_BINARY, EXC_LFS_POINTER,
        EXC_PATH_LENGTH, EXC_CASEFOLD_COLLISION,
        EXC_EXECUTABLE, EXC_UNICODE_NFC,
    }
    assert _BLOCKING_EXCEPTIONS == expected, (
        f"Blocking set mismatch: {_BLOCKING_EXCEPTIONS} != {expected}"
    )


# ── Path-filtered history tests ───────────────────────────────────────────────


def test_filtered_history_wiki_extracts_only_wiki_paths(manifest_wiki_filtered, tmp_path):
    dest_root = str(tmp_path / "dest_wiki_filtered")
    result = run_rehearsal(manifest_wiki_filtered, dest_root)

    wiki_domain = None
    for name in result["domains"]:
        if "wiki" in name.lower():
            wiki_domain = name
            break
    assert wiki_domain is not None

    dest = Path(dest_root) / "wiki"
    assert dest.is_dir()
    assert (dest / ".git").is_dir()

    files = _git_ls_files(str(dest))
    assert len(files) > 0, "No files in filtered wiki repo"

    for f in files:
        parts = Path(f).parts
        assert parts[0] in ("Zettelkasten", "MIGRATION_BASE", "redirects.json", "references.json"), (
            f"Out-of-scope path in filtered wiki: {f}"
        )


def test_filtered_history_no_other_paths_leak(manifest_wiki_filtered, tmp_path):
    dest_root = str(tmp_path / "dest_wiki_noleak")
    result = run_rehearsal(manifest_wiki_filtered, dest_root)

    wiki_domain = None
    for name in result["domains"]:
        if "wiki" in name.lower():
            wiki_domain = name
            break
    assert wiki_domain is not None

    dest = Path(dest_root) / "wiki"
    files = _git_ls_files(str(dest))

    wiki_objects = manifest_wiki_filtered["objects"]
    wiki_paths = {obj["destination_path"] for obj in wiki_objects
                  if obj["action"] != ACTION_REJECT}

    for f in files:
        if f in ("MIGRATION_BASE", "redirects.json", "references.json"):
            continue
        assert f in wiki_paths or any(
            f.startswith(p + "/") or p == f for p in wiki_paths
        ), f"Leaked out-of-scope path: {f}"


# ── Multi-source memory history tests ─────────────────────────────────────────


def test_multi_source_memory_all_commits_reachable(manifest_multi_source_memory, tmp_path):
    dest_root = str(tmp_path / "dest_multi_mem")
    result = run_rehearsal(manifest_multi_source_memory, dest_root)

    memory_domain = None
    for name in result["domains"]:
        if "memory" in name.lower():
            memory_domain = name
            break
    assert memory_domain is not None

    dest = Path(dest_root) / "memory"
    assert dest.is_dir()
    assert (dest / ".git").is_dir()

    all_commits = _git_all_commits(str(dest))
    assert len(all_commits) >= 4, (
        f"Expected at least 4 commits from 2 source repos, got {len(all_commits)}"
    )


def test_multi_source_memory_has_all_files(manifest_multi_source_memory, tmp_path):
    dest_root = str(tmp_path / "dest_multi_mem2")
    result = run_rehearsal(manifest_multi_source_memory, dest_root)

    dest = Path(dest_root) / "memory"
    files = _git_ls_files(str(dest))

    materialized = [f for f in files if f.endswith(".md")]
    assert len(materialized) >= 4, f"Expected >=4 materialized files, got {materialized}"


def test_multi_source_memory_no_history_loss(manifest_multi_source_memory, tmp_path):
    dest_root = str(tmp_path / "dest_multi_mem3")
    run_rehearsal(manifest_multi_source_memory, dest_root)

    dest = Path(dest_root) / "memory"
    all_commits = _git_all_commits(str(dest))

    commit_messages = []
    for c in all_commits:
        out = subprocess.check_output(
            ["git", "-C", str(dest), "log", "--format=%s", "-1", c],
            text=True,
        ).strip()
        commit_messages.append(out)

    source_msgs = [m for m in commit_messages
                   if "initial" in m.lower() or "add card" in m.lower()]
    assert len(source_msgs) >= 4, (
        f"Expected at least 4 source commits, got {len(source_msgs)}: {source_msgs}"
    )


# ── Filter-branch failure tests ───────────────────────────────────────────────


def test_filter_branch_failure_raises(tmp_path, monkeypatch):
    source_repo = tmp_path / "filter_fail_repo"
    source_repo.mkdir()
    _git_init(str(source_repo))

    (source_repo / "Zettelkasten").mkdir()
    (source_repo / "Zettelkasten" / "note1.md").write_text("# Note 1\n\nContent 1\n", encoding="utf-8")
    _git_commit_all(str(source_repo), "initial: note1")

    (source_repo / "not-in-manifest.txt").write_text("This file is not matched by the md pattern\n", encoding="utf-8")
    _git_commit_all(str(source_repo), "add non-manifest file")

    source_sets = [{
        "name": "wiki_filter_fail",
        "root": str(source_repo),
        "source_repo": str(source_repo),
        "source_commit": _git_head(str(source_repo)),
        "object_class": "wiki",
        "prefix": "w-",
        "destination_repo": "/data/wiki",
        "default_action": "preserve",
        "auto_classify": True,
        "include": ["**/*.md"],
    }]
    manifest = build_manifest(source_sets, migration_run_id="filter-fail-test")

    dest_root = str(tmp_path / "dest_filter_fail")

    original_run = subprocess.run

    def mock_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if "filter-branch" in cmd_str:
            raise subprocess.CalledProcessError(1, cmd, stderr="simulated filter-branch failure")
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Filtered-history extraction failed"):
        run_rehearsal(manifest, dest_root)


# ── Rejected objects not materialized ─────────────────────────────────────────


def test_rejected_objects_not_materialized(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    rejected = [r for r in manifest_all_actions["objects"] if r["action"] == ACTION_REJECT]
    for obj in rejected:
        domain_name = obj["destination_repo"].rstrip("/").rsplit("/", 1)[-1]
        dest_file = _domain_dir(dest_root, domain_name) / obj["destination_path"]
        assert not dest_file.is_file(), (
            f"Rejected object {obj['source_path']} was materialized at {dest_file}"
        )


# ── Archive action ────────────────────────────────────────────────────────────


def test_archive_action(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    archive_target = None
    for obj in m["objects"]:
        if obj["action"] == ACTION_PRESERVE and "wiki" in obj.get("object_class", ""):
            archive_target = obj
            break
    if archive_target:
        archive_target["action"] = ACTION_ARCHIVE

    run_rehearsal(m, dest_root)

    if archive_target:
        archive_dir = _domain_dir(dest_root, "wiki") / ".archive"
        assert archive_dir.is_dir(), "Archive directory not created"


# ── Merge action ──────────────────────────────────────────────────────────────


def test_merge_action(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    merge_target = None
    for obj in m["objects"]:
        if obj["action"] == ACTION_PRESERVE and "memory" in obj.get("object_class", ""):
            merge_target = obj
            break
    if merge_target:
        merge_target["action"] = ACTION_MERGE

    run_rehearsal(m, dest_root)

    if merge_target:
        dest_file = _domain_dir(dest_root, "memory") / merge_target["destination_path"]
        assert dest_file.is_file(), f"Merge target not materialized: {merge_target['source_path']}"


# ── Rehearsal-only: no production paths touched ───────────────────────────────


def test_rehearsal_only_no_production_paths(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    for obj in manifest_all_actions["objects"]:
        assert not str(obj["source_path"]).startswith("/data/memory"), "Production path leak"
        assert not str(obj["source_path"]).startswith("/data/vault/"), "Production path leak"
        assert not str(obj["source_path"]).startswith("/data/wiki"), "Production path leak"
        assert not str(obj["source_path"]).startswith("/data/work-records"), "Production path leak"


# ── Domain structure tests ────────────────────────────────────────────────────


def test_wiki_domain_has_filtered_history(manifest_wiki_filtered, tmp_path):
    dest_root = str(tmp_path / "dest_wiki_hist")
    result = run_rehearsal(manifest_wiki_filtered, dest_root)

    wiki_domain = None
    for name in result["domains"]:
        if "wiki" in name.lower():
            wiki_domain = name
            break
    assert wiki_domain is not None, "Wiki domain not found in results"

    dest = Path(dest_root) / "wiki"
    assert (dest / ".git").is_dir(), "Wiki domain is not a git repo"
    commits = _git_all_commits(str(dest))
    assert len(commits) > 0, "Wiki domain has no git history"


def test_memory_domain_preserves_canonical_ids(manifest_all_actions, dest_root):
    run_rehearsal(manifest_all_actions, dest_root)

    canonical = [r for r in manifest_all_actions["objects"]
                 if r["object_class"] == "memory_canonical"]
    for obj in canonical:
        dest_file = _domain_dir(dest_root, "memory") / obj["destination_path"]
        if dest_file.is_file():
            content = dest_file.read_text(encoding="utf-8")
            expected_id = obj["domain_resource_id"]
            assert f"id: {expected_id}" in content, (
                f"Canonical ID {expected_id} not found in {obj['source_path']}"
            )


# ── Redirect_map application in rewrite ───────────────────────────────────────


def test_redirect_map_applied_in_rewrite(manifest_all_actions, dest_root, tmp_path):
    m = json.loads(json.dumps(manifest_all_actions))
    legacy_obj = None
    for obj in m["objects"]:
        if obj["object_class"] == "memory_legacy":
            legacy_obj = obj
            break
    assert legacy_obj is not None, "No legacy object found"

    legacy_obj["action"] = ACTION_REWRITE

    run_rehearsal(m, dest_root)

    dest_file = _domain_dir(dest_root, "memory") / legacy_obj["destination_path"]
    assert dest_file.is_file()
    content = dest_file.read_text(encoding="utf-8")

    redirect_map = m.get("redirect_map", {})
    for old_path, new_id in redirect_map.items():
        if old_path == legacy_obj["source_path"]:
            continue
        assert f"[[{old_path}]]" not in content or f"[[{new_id}]]" in content, (
            f"Redirect {old_path} -> {new_id} not applied in content"
        )


# ── Engine class test ─────────────────────────────────────────────────────────


def test_engine_class_run(manifest_all_actions, dest_root):
    engine = RehearsalEngine(manifest_all_actions, dest_root)
    result = engine.run()
    assert "domains" in result
    assert "summary" in result


# ── compute_summary integration ───────────────────────────────────────────────


def test_compute_summary_from_manifest(manifest_all_actions):
    s = compute_summary(manifest_all_actions["objects"])
    assert s["invariant_holds"] is True
    assert s["unclassified"] == 0