"""Work Folder server configuration、search 与 public envelope 测试。"""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import katana_work_folder_mcp.server as server
from katana_kernel import MutationBrokenError
from katana_work_folder_mcp.reindex import render_index


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Work Folder Test"],
        cwd=root,
        check=True,
    )
    (root / ".gitkeep").write_text("", encoding="utf-8")
    (root / ".gitignore").write_text("/.katana/runtime/\n", encoding="utf-8")
    (root / "INDEX.md").write_text(render_index([]), encoding="utf-8")
    controls = root / ".katana"
    controls.mkdir()
    (controls / "tombstones.json").write_text(
        '{"tombstones": []}\n',
        encoding="utf-8",
    )
    (controls / "flat-layout.json").write_text(
        '{"layout": "flat-id-v1", "schema_version": 1}\n',
        encoding="utf-8",
    )
    (controls / "legacy-manifest-inventory.json").write_text(
        '{"manifests":[],"schema_version":1}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "init"],
        cwd=root,
        check=True,
    )


def test_configure_requires_existing_exact_git_root(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        server.configure(str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="existing Git"):
        server.configure(str(tmp_path))

    _init_repo(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    outer_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    with pytest.raises(ValueError, match="Git repository root"):
        server.configure(str(child))
    assert not (child / ".git").exists()
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == outer_head


def test_configure_binds_single_root_without_initializing(tmp_path):
    _init_repo(tmp_path)

    server.configure(str(tmp_path))

    assert server._repo_root == str(tmp_path.resolve())
    assert server._store is not None
    assert server._fs_tools is not None
    binding = server._kernel.get_binding("work-folder")
    assert binding.manifest.git_tracked is False
    assert binding.manifest.manifests_dir == str(
        tmp_path / ".katana" / "runtime" / "manifests"
    )
    assert binding.mutation_ledger.path == str(
        tmp_path / ".katana" / "runtime" / "mutations.sqlite"
    )
    tracked = subprocess.run(
        ["git", "ls-files", ".katana/manifests", ".katana/runtime"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert tracked == ""

    asyncio.run(
        server.wf_create(
            "lock-root",
            idempotency_key="outer-lock-root",
        )
    )
    assert (tmp_path / ".git/katana-governed.lock").is_file()
    assert not any(
        path.name == "katana-governed.lock"
        for path in tmp_path.glob("wf-*/.git/*")
    )


def test_configure_rejects_nested_git_metadata_without_mutation(tmp_path):
    _init_repo(tmp_path)
    nested = tmp_path / ".katana/control-archive/incident/.git"
    nested.mkdir(parents=True)
    (nested / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    with pytest.raises(ValueError, match="nested Git metadata"):
        server.configure(str(tmp_path))

    assert (nested / "HEAD").is_file()
    assert subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == head_before


def test_configure_requires_flat_canary_and_rejects_legacy_topology(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".katana" / "flat-layout.json").unlink()
    subprocess.run(["git", "add", "-u"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "remove canary"],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(ValueError, match="canary"):
        server.configure(str(tmp_path))

    (tmp_path / ".katana" / "flat-layout.json").write_text(
        '{"layout": "flat-id-v1", "schema_version": 1}\n',
        encoding="utf-8",
    )
    (tmp_path / "2026" / "07" / "29" / "legacy").mkdir(parents=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "restore canary and legacy tree"],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(ValueError, match="legacy|topology"):
        server.configure(str(tmp_path))


def test_configure_rejects_double_root_and_index_drift(tmp_path):
    _init_repo(tmp_path)
    double_root = tmp_path / "智元工作" / "工作记录"
    double_root.mkdir(parents=True)
    (double_root / ".gitkeep").write_text("", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "double root"],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(ValueError, match="legacy|topology"):
        server.configure(str(tmp_path))

    subprocess.run(
        ["git", "rm", "-qr", "智元工作"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "INDEX.md").write_text("# stale\n", encoding="utf-8")
    subprocess.run(["git", "add", "INDEX.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "stale index"],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(ValueError, match="INDEX"):
        server.configure(str(tmp_path))


def test_configure_rejects_legacy_tracked_manifest_directory(tmp_path):
    _init_repo(tmp_path)
    manifests = tmp_path / ".katana" / "manifests"
    manifests.mkdir()
    (manifests / "old.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "legacy manifest"],
        cwd=tmp_path,
        check=True,
    )

    with pytest.raises(ValueError, match="legacy manifest"):
        server.configure(str(tmp_path))


def test_configure_requires_exact_legacy_manifest_inventory(tmp_path):
    _init_repo(tmp_path)
    inventory = tmp_path / ".katana/legacy-manifest-inventory.json"
    inventory.unlink()
    subprocess.run(["git", "add", "-u"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "remove legacy inventory"],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(ValueError, match="legacy manifest inventory"):
        server.configure(str(tmp_path))

    archive = tmp_path / ".katana/legacy-manifests/root/old.json"
    archive.parent.mkdir(parents=True)
    archive.write_text("{}\n", encoding="utf-8")
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "manifests": [
                    {
                        "source_repo_path": ".katana/manifests/old.json",
                        "archive_repo_path": (
                            ".katana/legacy-manifests/root/old.json"
                        ),
                        "sha256": "0" * 64,
                        "size": 3,
                        "git_tracked": True,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "add corrupt manifest inventory"],
        cwd=tmp_path,
        check=True,
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        server.configure(str(tmp_path))


def test_configure_fails_closed_when_runtime_ledger_was_lost(tmp_path):
    _init_repo(tmp_path)
    server.configure(str(tmp_path))
    asyncio.run(
        server.wf_create(
            "receipt",
            idempotency_key="create-receipt",
        )
    )
    ledger = tmp_path / ".katana" / "runtime" / "mutations.sqlite"
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{ledger}{suffix}").unlink(missing_ok=True)

    with pytest.raises(MutationBrokenError, match="ledger is incomplete"):
        server.configure(str(tmp_path))


def test_do_search_shapes_only_flat_id_locators(monkeypatch):
    captured = {}

    def fake_search(query, *, top_k=10):
        captured.update(query=query, top_k=top_k)
        return SimpleNamespace(
            results=[
                SimpleNamespace(
                    path="wf-abc123/findings/note.md",
                    score=0.85,
                    title="Note",
                    snippet="match",
                ),
                SimpleNamespace(
                    path="2026/07/29/legacy.md",
                    score=0.7,
                    title="Legacy",
                    snippet="ignored",
                ),
                SimpleNamespace(
                    path="INDEX.md",
                    score=0.5,
                    title="Index",
                    snippet="ignored",
                ),
            ]
        )

    monkeypatch.setattr(server.vault_search, "search", fake_search)

    result = server._do_search("工作记录", 5)

    assert captured == {"query": "工作记录", "top_k": 5}
    assert result == [
        {
            "folder_id": "wf-abc123",
            "filename": "findings/note.md",
            "score": 0.85,
            "title": "Note",
            "snippet": "match",
        }
    ]


def test_public_payload_drops_internal_locator_fields_and_extracts_mutation_id(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))
    raw = {
        "folder_id": "wf-abc123",
        "filename": "notes.md",
        "path": f"{tmp_path}/wf-abc123/notes.md",
        "changed_paths": ["wf-abc123/notes.md"],
        "manifest": {"manifest_id": "tx-123", "path": str(tmp_path)},
        "nested": {
            "resource_id": "legacy",
            "virtual_path": "wf-abc123/notes.md",
            "message": f"failed under {tmp_path}",
        },
    }

    result = server._public_payload(raw)

    assert result["folder_id"] == "wf-abc123"
    assert result["filename"] == "notes.md"
    assert result["mutation_id"] == "tx-123"
    rendered = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert not {
        "path",
        "changed_paths",
        "manifest",
        "resource_id",
        "virtual_path",
    } & result.keys()
    assert result["nested"] == {"message": "failed under <work-folder-root>"}


def test_unconfigured_guards(monkeypatch):
    monkeypatch.setattr(server, "_store", None)
    monkeypatch.setattr(server, "_fs_tools", None)
    monkeypatch.setattr(server, "_repo_root", None)

    with pytest.raises(RuntimeError, match="configure"):
        server._require_store()
    with pytest.raises(RuntimeError, match="configure"):
        server._require_fs_tools()


def test_broken_mutation_drops_rollback_locator_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_repo_root", str(tmp_path))

    result = server._server_mutation(
        lambda: (_ for _ in ()).throw(
            MutationBrokenError(
                "manual recovery required",
                {
                    "state": "BROKEN",
                    "paths": [
                        str(tmp_path / "wf-abc123" / "progress.md"),
                    ],
                },
            )
        )
    )

    assert result["code"] == "BROKEN"
    assert "rollback" not in result
    assert str(tmp_path) not in json.dumps(result)
