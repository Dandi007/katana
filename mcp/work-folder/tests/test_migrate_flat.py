from __future__ import annotations

import copy
import fcntl
import hashlib
import re
import subprocess
from pathlib import Path

import pytest

import scripts.migrate_flat as migration
from scripts.migrate_flat import (
    MigrationError,
    apply_plan,
    build_inventory,
    build_parser,
    build_plan,
    canonical_json,
    deterministic_id,
    maintenance_sentinel_payload,
    verify_plan,
)
from katana_work_folder_mcp import server


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit_all(repo: Path, message: str = "fixture") -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _brief(title: str, resource_id: str | None) -> str:
    id_line = f"id: {resource_id}\n" if resource_id is not None else ""
    return (
        "---\n"
        f"{id_line}"
        f"title: {title}\n"
        "status: active\n"
        "created: 2026-07-01\n"
        "updated: 2026-07-29\n"
        "tags:\n"
        "- migration\n"
        "---\n"
        "\n"
        f"**Goal:** migrate {title}\n"
        "\n"
        "Keep this body byte-for-byte.\n"
    )


def _topic(
    legacy_root: Path,
    locator: str,
    *,
    title: str,
    resource_id: str | None,
) -> Path:
    folder = legacy_root / locator
    folder.mkdir(parents=True)
    (folder / "_brief.md").write_text(
        _brief(title, resource_id),
        encoding="utf-8",
    )
    (folder / "progress.md").write_text(
        "# Progress\n\n- unchanged\n",
        encoding="utf-8",
    )
    nested = folder / "artifacts"
    nested.mkdir()
    (nested / "proof.txt").write_text("proof\n", encoding="utf-8")
    return folder


@pytest.fixture
def flat_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "migration-test@example.com")
    _git(repo, "config", "user.name", "Migration Test")

    legacy_root = repo / "records"
    legacy_root.mkdir()

    (repo / "AGENTS.md").write_text("root agents\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("root claude\n", encoding="utf-8")
    (repo / ".gitignore").write_text(
        ".katana/runtime.bin\n",
        encoding="utf-8",
    )
    (repo / "MIGRATION_BASE.json").write_text("{}\n", encoding="utf-8")
    root_katana = repo / ".katana"
    root_katana.mkdir()
    (root_katana / "ledger.sqlite").write_bytes(b"synthetic-ledger")
    (root_katana / "runtime.bin").write_bytes(b"ignored-runtime")

    (legacy_root / "INDEX.md").write_text("# INDEX\n", encoding="utf-8")
    (legacy_root / "AGENTS.md").write_text("legacy agents\n", encoding="utf-8")
    (legacy_root / "CLAUDE.md").write_text("legacy claude\n", encoding="utf-8")
    legacy_katana = legacy_root / ".katana"
    legacy_katana.mkdir()
    legacy_manifests = legacy_katana / "manifests"
    legacy_manifests.mkdir()
    (legacy_manifests / "manifest.json").write_text("{}\n", encoding="utf-8")

    _topic(
        legacy_root,
        "2026/07/01/canonical",
        title="Canonical",
        resource_id="wf-a1b2c3",
    )
    _topic(
        legacy_root,
        "2026/07/02/legacy",
        title="Legacy",
        resource_id="2026-0702-legacy",
    )
    _topic(
        legacy_root,
        "2026/07/03/missing-id",
        title="Missing ID",
        resource_id=None,
    )
    _commit_all(repo)
    return repo, legacy_root


def _make_plan(repo: Path, legacy_root: Path, repairs: dict | None = None) -> dict:
    inventory = build_inventory(repo, legacy_root)
    assert inventory["ok"] is True
    return build_plan(inventory, repairs=repairs)


def _write_sentinel(tmp_path: Path, plan: dict) -> Path:
    sentinel = tmp_path / "maintenance.json"
    sentinel.write_bytes(canonical_json(maintenance_sentinel_payload(plan)))
    return sentinel


def _without_id_line(text: str) -> str:
    return re.sub(r"(?m)^id:[^\n]*\n", "", text)


def test_cli_exposes_inventory_plan_sentinel_apply_verify() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
    )
    assert set(subparsers.choices) == {
        "inventory",
        "plan",
        "sentinel",
        "apply",
        "verify",
    }
    sentinel_parser = subparsers.choices["sentinel"]
    expected_hash = next(
        action
        for action in sentinel_parser._actions
        if action.dest == "expected_plan_hash"
    )
    assert expected_hash.required is True


def test_inventory_accepts_only_date_topic_anchors_and_classifies_controls(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo
    inventory = build_inventory(repo, legacy_root)

    assert inventory["ok"] is True
    assert [item["old_locator"] for item in inventory["topics"]] == [
        "2026/07/01/canonical",
        "2026/07/02/legacy",
        "2026/07/03/missing-id",
    ]
    assert {item["brief_state"] for item in inventory["topics"]} == {"valid"}
    controls = {item["repo_relative_path"] for item in inventory["controls"]}
    assert {
        ".katana",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "MIGRATION_BASE.json",
        "records/.katana",
        "records/AGENTS.md",
        "records/CLAUDE.md",
        "records/INDEX.md",
    } <= controls
    assert inventory["source_head"] == _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("relative_path", "error_code"),
    [
        ("records/rogue.md", "UNKNOWN_LEGACY_ROOT_PAYLOAD"),
        ("README.md", "UNKNOWN_REPO_ROOT_PAYLOAD"),
        ("records/2026/13/01/bad/progress.md", "INVALID_MONTH"),
        ("records/2026/07/not-a-day/bad/progress.md", "INVALID_DAY"),
    ],
)
def test_inventory_rejects_unknown_root_and_invalid_anchor_payload(
    flat_repo: tuple[Path, Path],
    relative_path: str,
    error_code: str,
) -> None:
    repo, legacy_root = flat_repo
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("unexpected\n", encoding="utf-8")
    _commit_all(repo, f"add {relative_path}")

    inventory = build_inventory(repo, legacy_root)

    assert inventory["ok"] is False
    assert error_code in {error["code"] for error in inventory["errors"]}
    with pytest.raises(MigrationError, match=error_code):
        build_plan(inventory)


def test_inventory_rejects_destination_overlap(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo
    overlap = repo / "wf-deadbe"
    overlap.mkdir()
    (overlap / "progress.md").write_text("partial migration\n", encoding="utf-8")
    _commit_all(repo, "partial destination")

    inventory = build_inventory(repo, legacy_root)

    assert inventory["ok"] is False
    assert "DESTINATION_OVERLAP" in {
        error["code"] for error in inventory["errors"]
    }


def test_inventory_rejects_nested_git_metadata_anywhere_below_root(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo
    nested = legacy_root / ".katana/control-archive/incident/.git"
    nested.mkdir(parents=True)
    (nested / "HEAD").write_text(
        "ref: refs/heads/main\n",
        encoding="utf-8",
    )

    inventory = build_inventory(repo, legacy_root)

    assert inventory["ok"] is False
    assert {
        (error["code"], error["locator"])
        for error in inventory["errors"]
    } >= {
        (
            "NESTED_GIT_METADATA",
            "records/.katana/control-archive/incident/.git",
        )
    }
    with pytest.raises(MigrationError, match="NESTED_GIT_METADATA"):
        build_plan(inventory)


def test_plan_is_byte_deterministic_and_maps_every_topic(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo

    first = _make_plan(repo, legacy_root)
    second = _make_plan(repo, legacy_root)

    assert canonical_json(first) == canonical_json(second)
    assert len(first["map"]) == 3
    by_locator = {item["old_locator"]: item for item in first["map"]}
    assert by_locator["2026/07/01/canonical"]["new_id"] == "wf-a1b2c3"
    assert by_locator["2026/07/01/canonical"]["reason"] == "canonical-id"
    assert by_locator["2026/07/02/legacy"]["reason"] == "legacy-id"
    assert by_locator["2026/07/03/missing-id"]["reason"] == "missing-id"
    assert len({item["new_id"] for item in first["map"]}) == 3
    assert all(
        re.fullmatch(r"wf-[0-9a-f]{6}", item["new_id"])
        for item in first["map"]
    )
    assert all(item["content_hashes"] for item in first["map"])
    assert hashlib.sha256(
        canonical_json({k: v for k, v in first.items() if k != "plan_hash"})
    ).hexdigest() == first["plan_hash"]


def test_sentinel_rejects_plan_without_matching_approved_hash(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)

    approved = maintenance_sentinel_payload(
        plan,
        expected_plan_hash=plan["plan_hash"],
    )

    assert approved["plan_hash"] == plan["plan_hash"]
    with pytest.raises(MigrationError, match="PLAN_HASH_CAS_MISMATCH"):
        maintenance_sentinel_payload(
            plan,
            expected_plan_hash="f" * 64,
        )


def test_deterministic_id_uses_domain_separation_and_collision_counter() -> None:
    first, first_counter = deterministic_id("2026/07/02/topic", set())
    second, second_counter = deterministic_id(
        "2026/07/02/topic",
        {first},
    )

    assert first_counter == 0
    assert second_counter == 1
    assert first != second
    assert first == deterministic_id("2026/07/02/topic", set())[0]
    assert first != deterministic_id("2026/07/02/other", set())[0]


def test_missing_and_parse_error_briefs_require_explicit_repair_metadata(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "migration-test@example.com")
    _git(repo, "config", "user.name", "Migration Test")
    legacy_root = repo / "records"

    missing = legacy_root / "2026/07/04/missing-brief"
    missing.mkdir(parents=True)
    (missing / "progress.md").write_text("# progress\n", encoding="utf-8")

    broken = legacy_root / "2026/07/05/parse-error"
    broken.mkdir(parents=True)
    broken_brief = broken / "_brief.md"
    broken_brief.write_text(
        '---\ntitle: "unterminated\n---\n\n**Goal:** broken\n',
        encoding="utf-8",
    )
    (broken / "progress.md").write_text("# progress\n", encoding="utf-8")
    _commit_all(repo)

    inventory = build_inventory(repo, legacy_root)
    assert inventory["ok"] is True
    assert {
        item["old_locator"]: item["brief_state"]
        for item in inventory["topics"]
    } == {
        "2026/07/04/missing-brief": "missing",
        "2026/07/05/parse-error": "parse_error",
    }

    with pytest.raises(MigrationError, match="REPAIR_METADATA_REQUIRED"):
        build_plan(inventory)

    broken_hash = hashlib.sha256(broken_brief.read_bytes()).hexdigest()
    repairs = {
        "2026/07/04/missing-brief": {
            "state": "missing",
            "brief_text": _brief("Missing Brief", None),
        },
        "2026/07/05/parse-error": {
            "state": "parse_error",
            "expected_sha256": broken_hash,
            "brief_text": _brief("Parse Error", "legacy-broken"),
        },
    }
    plan = build_plan(inventory, repairs=repairs)

    reasons = {item["reason"] for item in plan["map"]}
    assert reasons == {"repair-missing-brief", "repair-parse-error"}


@pytest.mark.parametrize(
    "broken_brief",
    [
        _brief("Missing Created", "wf-a1b2c3").replace(
            "created: 2026-07-01\n",
            "",
        ),
        _brief("Invalid Status", "wf-a1b2c3").replace(
            "status: active\n",
            "status: brainstorming\n",
        ),
        _brief("Missing Goal", "wf-a1b2c3").replace(
            "**Goal:** migrate Missing Goal",
            "**Goal:**",
        ),
    ],
)
def test_invalid_brief_metadata_requires_explicit_repair_without_guessing(
    flat_repo: tuple[Path, Path],
    broken_brief: str,
) -> None:
    repo, legacy_root = flat_repo
    target = legacy_root / "2026/07/01/canonical/_brief.md"
    target.write_text(broken_brief, encoding="utf-8")
    _commit_all(repo, "break brief metadata")

    inventory = build_inventory(repo, legacy_root)
    topic = next(
        item
        for item in inventory["topics"]
        if item["old_locator"] == "2026/07/01/canonical"
    )

    assert topic["brief_state"] == "invalid_metadata"
    with pytest.raises(MigrationError, match="REPAIR_METADATA_REQUIRED"):
        build_plan(inventory)


def test_apply_dry_run_is_byte_identical_and_does_not_mutate(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)

    kwargs = {
        "plan": plan,
        "repo_root": repo,
        "legacy_root": legacy_root,
        "expected_head": plan["source_head"],
        "expected_plan_hash": plan["plan_hash"],
        "maintenance_sentinel": sentinel,
        "dry_run": True,
    }
    first = canonical_json(apply_plan(**kwargs))
    second = canonical_json(apply_plan(**kwargs))

    assert first == second
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert (legacy_root / "2026/07/01/canonical").is_dir()
    assert not (repo / "wf-a1b2c3").exists()


def test_apply_holds_governed_repository_lock_through_topic_moves(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    original = migration._apply_topic
    observed_locked = False

    def assert_locked(repo_arg: Path, item: dict) -> None:
        nonlocal observed_locked
        lock_path = repo / ".git/katana-governed.lock"
        with lock_path.open("a+b") as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed_locked = True
        original(repo_arg, item)

    monkeypatch.setattr(migration, "_apply_topic", assert_locked)
    apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )

    assert observed_locked is True


@pytest.mark.parametrize(
    "gate",
    ["dirty", "head", "plan_hash", "missing_sentinel", "wrong_sentinel"],
)
def test_apply_requires_all_cas_and_maintenance_gates(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
    gate: str,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    expected_head = plan["source_head"]
    expected_plan_hash = plan["plan_hash"]

    if gate == "dirty":
        (repo / "AGENTS.md").write_text("dirty\n", encoding="utf-8")
    elif gate == "head":
        expected_head = "0" * 40
    elif gate == "plan_hash":
        expected_plan_hash = "f" * 64
    elif gate == "missing_sentinel":
        sentinel = tmp_path / "does-not-exist.json"
    elif gate == "wrong_sentinel":
        sentinel.write_text('{"maintenance": false}\n', encoding="utf-8")

    with pytest.raises(MigrationError):
        apply_plan(
            plan,
            repo,
            legacy_root,
            expected_head=expected_head,
            expected_plan_hash=expected_plan_hash,
            maintenance_sentinel=sentinel,
            dry_run=True,
        )


def test_apply_rejects_ignored_source_inventory_drift_before_any_move(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)

    exclude = repo / ".git/info/exclude"
    exclude.write_text(
        exclude.read_text(encoding="utf-8")
        + "\nrecords/2026/07/04/ignored-topic/\n",
        encoding="utf-8",
    )
    ignored = legacy_root / "2026/07/04/ignored-topic"
    ignored.mkdir(parents=True)
    (ignored / "progress.md").write_text("# ignored\n", encoding="utf-8")
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == ""

    with pytest.raises(MigrationError, match="INVENTORY_CAS_MISMATCH"):
        apply_plan(
            plan,
            repo,
            legacy_root,
            expected_head=plan["source_head"],
            expected_plan_hash=plan["plan_hash"],
            maintenance_sentinel=sentinel,
            dry_run=True,
        )

    assert (legacy_root / "2026/07/01/canonical").is_dir()
    assert not (repo / "wf-a1b2c3").exists()


def test_apply_rejects_rehashed_plan_with_unsafe_paths(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = copy.deepcopy(_make_plan(repo, legacy_root))
    plan["map"][0]["new_id"] = "wf-deadbe"
    plan["map"][0]["new_repo_path"] = "../wf-deadbe"
    unsigned = {key: value for key, value in plan.items() if key != "plan_hash"}
    plan["plan_hash"] = hashlib.sha256(canonical_json(unsigned)).hexdigest()
    sentinel = _write_sentinel(tmp_path, plan)

    with pytest.raises(MigrationError, match="INVALID_PLAN"):
        apply_plan(
            plan,
            repo,
            legacy_root,
            expected_head=plan["source_head"],
            expected_plan_hash=plan["plan_hash"],
            maintenance_sentinel=sentinel,
            dry_run=True,
        )

    assert (legacy_root / "2026/07/01/canonical").is_dir()
    assert not (repo.parent / "wf-deadbe").exists()


def test_apply_moves_with_git_updates_only_brief_id_and_verifies(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    legacy_brief = (
        legacy_root / "2026/07/02/legacy/_brief.md"
    ).read_text(encoding="utf-8")
    controls_before = {
        item: (repo / item).read_bytes()
        for item in [
            "AGENTS.md",
            "CLAUDE.md",
            ".gitignore",
            "MIGRATION_BASE.json",
            "records/INDEX.md",
            "records/AGENTS.md",
            "records/CLAUDE.md",
        ]
    }
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)

    result = apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    verification = verify_plan(plan, repo, legacy_root)

    assert result["applied"] is True
    assert verification["ok"] is True
    assert verification["source_anchor_count"] == 0
    assert verification["unexpected_diff_paths"] == []
    for item in plan["map"]:
        destination = repo / item["new_id"]
        assert destination.is_dir()
        brief = destination / "_brief.md"
        assert re.search(
            rf"(?m)^id: {re.escape(item['new_id'])}$",
            brief.read_text(encoding="utf-8"),
        )
        assert not (legacy_root / item["old_locator"]).exists()

    legacy_item = next(
        item for item in plan["map"]
        if item["old_locator"] == "2026/07/02/legacy"
    )
    migrated_brief = (
        repo / legacy_item["new_id"] / "_brief.md"
    ).read_text(encoding="utf-8")
    assert _without_id_line(migrated_brief) == _without_id_line(legacy_brief)
    actions_by_source = {
        action["source_repo_path"]: action
        for action in plan["control_actions"]
        if action["source_repo_path"]
    }
    for source, content in controls_before.items():
        action = actions_by_source[source]
        archived = repo / action["destination_repo_path"]
        assert archived.read_bytes() == content
    assert not (repo / "AGENTS.md").exists()
    assert not legacy_root.exists()
    assert (repo / ".katana/flat-layout.json").is_file()
    assert (repo / ".katana/legacy-manifest-inventory.json").is_file()
    assert not (repo / ".katana/manifests").exists()


def test_verify_rejects_post_apply_content_tampering(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    item = plan["map"][0]
    (repo / item["new_id"] / "progress.md").write_text(
        "tampered\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="POST_HASH_MISMATCH"):
        verify_plan(plan, repo, legacy_root)


def test_verify_rejects_ignored_control_tampering(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    (repo / ".katana/runtime.bin").write_bytes(b"tampered-runtime")
    assert ".katana/runtime.bin" not in _git(
        repo,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )

    with pytest.raises(MigrationError, match="CONTROL_CHANGED"):
        verify_plan(plan, repo, legacy_root)


def test_verify_and_server_reject_nested_git_in_migrated_tree(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    nested = repo / ".katana/control-archive/injected/.git"
    nested.mkdir(parents=True)
    (nested / "HEAD").write_text(
        "ref: refs/heads/main\n",
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="NESTED_GIT_METADATA"):
        verify_plan(plan, repo, legacy_root)
    with pytest.raises(ValueError, match="nested Git metadata"):
        server.configure(str(repo))


def test_empty_topic_is_inventoried_and_requires_explicit_brief_repair(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo
    empty = legacy_root / "2026/07/04/empty-topic"
    empty.mkdir(parents=True)

    inventory = build_inventory(repo, legacy_root)
    topic = next(
        item
        for item in inventory["topics"]
        if item["old_locator"] == "2026/07/04/empty-topic"
    )
    assert inventory["ok"] is True
    assert topic["is_empty"] is True
    assert topic["brief_state"] == "missing"
    with pytest.raises(MigrationError, match="REPAIR_METADATA_REQUIRED"):
        build_plan(inventory)

    plan = build_plan(
        inventory,
        repairs={
            "2026/07/04/empty-topic": {
                "state": "missing",
                "brief_text": _brief("Empty Topic", None),
            }
        },
    )
    assert any(
        item["old_locator"] == "2026/07/04/empty-topic"
        for item in plan["map"]
    )


def test_inventory_normalizes_exact_double_root_and_rejects_collision(
    flat_repo: tuple[Path, Path],
) -> None:
    repo, legacy_root = flat_repo
    source = legacy_root / "2026/07/03/missing-id"
    nested = (
        legacy_root
        / "智元工作/工作记录/2026/07/03/missing-id"
    )
    nested.parent.mkdir(parents=True)
    _git(
        repo,
        "mv",
        source.relative_to(repo).as_posix(),
        nested.relative_to(repo).as_posix(),
    )
    _commit_all(repo, "move one topic into accidental double root")

    inventory = build_inventory(repo, legacy_root)
    topic = next(
        item
        for item in inventory["topics"]
        if item["old_locator"] == "2026/07/03/missing-id"
    )
    assert inventory["ok"] is True
    assert topic["source_repo_path"].startswith(
        "records/智元工作/工作记录/"
    )

    duplicate = (
        legacy_root
        / "智元工作/工作记录/2026/07/01/canonical"
    )
    _topic(
        legacy_root / "智元工作/工作记录",
        "2026/07/01/canonical",
        title="Duplicate",
        resource_id="wf-dedede",
    )
    assert duplicate.is_dir()
    _commit_all(repo, "add logical locator collision")
    rejected = build_inventory(repo, legacy_root)
    assert rejected["ok"] is False
    assert "DUPLICATE_SOURCE_LOCATOR" in {
        error["code"] for error in rejected["errors"]
    }


def test_tombstones_are_reserved_merged_and_live_overlap_fails_closed(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    tombstone_path = legacy_root / ".katana/tombstones.json"
    tombstone_path.write_text(
        '{"tombstones":["wf-c885d5"]}\n',
        encoding="utf-8",
    )
    _commit_all(repo, "add legacy tombstone")

    plan = _make_plan(repo, legacy_root)
    assert plan["tombstones"] == ["wf-c885d5"]
    assert "wf-c885d5" not in {item["new_id"] for item in plan["map"]}
    sentinel = _write_sentinel(tmp_path, plan)
    apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    assert (
        migration._load_json(repo / ".katana/tombstones.json")["tombstones"]
        == ["wf-c885d5"]
    )

    # A separate fixture state proves live/tombstone overlap is not repaired.
    repo2 = tmp_path / "overlap-repo"
    repo2.mkdir()
    _git(repo2, "init", "-q")
    _git(repo2, "config", "user.email", "migration-test@example.com")
    _git(repo2, "config", "user.name", "Migration Test")
    legacy2 = repo2 / "records"
    _topic(
        legacy2,
        "2026/07/01/live",
        title="Live",
        resource_id="wf-a1b2c3",
    )
    controls = legacy2 / ".katana"
    controls.mkdir()
    (controls / "tombstones.json").write_text(
        '{"tombstones":["wf-a1b2c3"]}\n',
        encoding="utf-8",
    )
    _commit_all(repo2)
    overlap = build_inventory(repo2, legacy2)
    assert "LIVE_ID_TOMBSTONED" in {
        error["code"] for error in overlap["errors"]
    }


def test_runtime_dot_dirs_are_archived_and_binary_is_api_reachable(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    topic = legacy_root / "2026/07/01/canonical"
    runtime = topic / ".superpowers"
    runtime.mkdir()
    (runtime / "cache.bin").write_bytes(b"\x00\xff\x10")
    github = topic / ".github"
    github.mkdir()
    (github / "workflow.yml").write_text("name: test\n", encoding="utf-8")
    _commit_all(repo, "add dot payload")

    plan = _make_plan(repo, legacy_root)
    item = next(
        entry
        for entry in plan["map"]
        if entry["old_locator"] == "2026/07/01/canonical"
    )
    binary = next(
        record
        for record in item["content_hashes"]
        if record["source_relative_path"] == ".superpowers/cache.bin"
    )
    assert binary["relative_path"] == "archive/runtime/superpowers/cache.bin"
    assert binary["api_tool"] == "fs_read_bytes"
    sentinel = _write_sentinel(tmp_path, plan)
    result = apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    assert result["verification"]["api_reachability"]["fs_read_bytes"] == 1
    assert (
        repo / item["new_id"] / "archive/runtime/superpowers/cache.bin"
    ).read_bytes() == b"\x00\xff\x10"
    assert (repo / item["new_id"] / ".github/workflow.yml").is_file()


def test_apply_resumes_exact_plan_from_external_checkpoint(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    checkpoint = tmp_path / "migration-checkpoint.json"
    original = migration._apply_control_action
    calls = 0

    def interrupt_after_one(repo_arg: Path, action: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interruption")
        original(repo_arg, action)

    monkeypatch.setattr(
        migration,
        "_apply_control_action",
        interrupt_after_one,
    )
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        apply_plan(
            plan,
            repo,
            legacy_root,
            expected_head=plan["source_head"],
            expected_plan_hash=plan["plan_hash"],
            maintenance_sentinel=sentinel,
            checkpoint_path=checkpoint,
        )
    payload = migration._load_json(checkpoint)
    assert payload["status"] == "applying"
    assert len(payload["completed_controls"]) == 1

    monkeypatch.setattr(migration, "_apply_control_action", original)
    orphan = repo / ".INDEX.md.migration-tmp-hard-crash"
    orphan.write_text("partial\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="UNEXPECTED_RESUME_DIFF"):
        apply_plan(
            plan,
            repo,
            legacy_root,
            expected_head=plan["source_head"],
            expected_plan_hash=plan["plan_hash"],
            maintenance_sentinel=sentinel,
            checkpoint_path=checkpoint,
        )
    orphan.unlink()
    resumed = apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
        checkpoint_path=checkpoint,
    )
    assert resumed["verification"]["ok"] is True
    assert migration._load_json(checkpoint)["status"] == "verified"


def test_migrated_and_committed_repository_passes_server_startup_gate(
    flat_repo: tuple[Path, Path],
    tmp_path: Path,
) -> None:
    repo, legacy_root = flat_repo
    plan = _make_plan(repo, legacy_root)
    sentinel = _write_sentinel(tmp_path, plan)
    apply_plan(
        plan,
        repo,
        legacy_root,
        expected_head=plan["source_head"],
        expected_plan_hash=plan["plan_hash"],
        maintenance_sentinel=sentinel,
    )
    _commit_all(repo, "flat cutover")

    server.configure(str(repo))

    assert server._repo_root == str(repo)
    assert server._kernel is not None
