"""ID-only MCP thin-shell routing tests."""

import asyncio
import datetime
import inspect

import katana_work_folder_mcp.server as server


def _run(coro):
    return asyncio.run(coro)


class FakeStore:
    def __init__(self):
        self.calls = []

    def create(self, topic, now_fn, expected_base_sha=None):
        self.calls.append(("create", topic, now_fn(), expected_base_sha))
        return {
            "created": True,
            "folder_id": "wf-abc123",
            "id": "wf-abc123",
            "changed_paths": ["wf-abc123/_brief.md"],
            "manifest": {"manifest_id": "tx-create"},
            "git": {"committed": True, "detail": "a" * 40},
        }

    def save(
        self,
        folder_id,
        now_fn,
        summary="checkpoint",
        context_snapshot=None,
        resume_fields=None,
        golden_order_additions=None,
        findings_addition=None,
        expected_base_sha=None,
    ):
        self.calls.append(
            (
                "save",
                folder_id,
                summary,
                context_snapshot,
                resume_fields,
                golden_order_additions,
                findings_addition,
                expected_base_sha,
            )
        )
        return {
            "saved": True,
            "folder_id": folder_id,
            "written": ["progress.md"],
            "manifest": {"manifest_id": "tx-save"},
        }

    def resume(self, folder_id, now_fn, probe_fn=None, expected_base_sha=None):
        self.calls.append(("resume", folder_id, now_fn(), expected_base_sha))
        return {
            "ok": True,
            "folder_id": folder_id,
            "blocked": False,
            "manifest": {"manifest_id": "tx-resume"},
        }

    def reindex(self, dry_run=False, expected_base_sha=None):
        self.calls.append(("reindex", dry_run, expected_base_sha))
        return {
            "indexed": 3,
            "skipped": 0,
            "errors": [],
            "index_path": "/physical/INDEX.md",
            "manifest": {"manifest_id": "tx-reindex"},
        }


class FakeFSTools:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            folder_id = kwargs.get("folder_id")
            if folder_id is None and args:
                folder_id = args[0]
            return {
                "ok": True,
                "folder_id": folder_id,
                "filename": kwargs.get("filename", args[1] if len(args) > 1 else ""),
                "path": "/physical/secret",
            }

        return call


def test_safe_resume_fields_keeps_only_semantic_agent_fields():
    assert server._safe_resume_fields(None) is None
    assert server._safe_resume_fields({}) is None
    assert server._safe_resume_fields(
        {
            "goal": "g",
            "phase": "p",
            "status": "s",
            "key_context": "ctx",
            "decisions": "d",
            "issues": "i",
            "lessons": "l",
            "wf_abs": "/secret",
            "now": "caller-controlled",
            "evil": "drop",
        }
    ) == {
        "goal": "g",
        "phase": "p",
        "status": "s",
        "key_context": "ctx",
        "decisions": "d",
        "issues": "i",
        "lessons": "l",
    }


def test_registered_tools_have_id_only_surface():
    names = {tool.name for tool in _run(server.mcp.list_tools())}
    assert {
        "wf_search",
        "wf_create",
        "wf_list",
        "wf_save",
        "wf_resume",
        "wf_reindex",
        "fs_capabilities",
        "fs_resolve",
        "fs_stat",
        "fs_list",
        "fs_read",
        "fs_create",
        "fs_write",
        "fs_edit",
        "fs_copy",
        "fs_rename",
        "fs_delete",
        "fs_batch",
    } <= names
    assert "fs_glob" not in names
    assert list(inspect.signature(server.wf_save).parameters)[0] == "folder_id"
    assert list(inspect.signature(server.fs_copy).parameters)[:4] == [
        "source_folder_id",
        "source_filename",
        "dest_folder_id",
        "dest_filename",
    ]


def test_lifecycle_tools_route_opaque_id_and_redact_internal_fields(monkeypatch):
    store = FakeStore()
    fixed_now = datetime.datetime(2026, 7, 29, 16, 0)
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server, "_now", lambda: fixed_now)

    created = _run(server.wf_create("topic", expected_base_sha="1" * 40))
    saved = _run(
        server.wf_save(
            "wf-abc123",
            summary="checkpoint",
            resume_fields={"goal": "goal", "wf_abs": "/secret"},
            expected_base_sha="2" * 40,
        )
    )
    resumed = _run(server.wf_resume("wf-abc123", expected_base_sha="3" * 40))
    indexed = _run(server.wf_reindex(expected_base_sha="4" * 40))

    assert store.calls[0] == ("create", "topic", fixed_now, "1" * 40)
    assert store.calls[1][0:3] == ("save", "wf-abc123", "checkpoint")
    assert store.calls[1][4] == {"goal": "goal"}
    assert store.calls[1][-1] == "2" * 40
    assert store.calls[2] == ("resume", "wf-abc123", fixed_now, "3" * 40)
    assert store.calls[3] == ("reindex", False, "4" * 40)

    assert created["folder_id"] == "wf-abc123"
    assert created["mutation_id"] == "tx-create"
    assert saved["folder_id"] == "wf-abc123"
    assert resumed["folder_id"] == "wf-abc123"
    assert indexed["mutation_id"] == "tx-reindex"
    for result in (created, saved, resumed, indexed):
        assert "path" not in result
        assert "index_path" not in result
        assert "manifest" not in result


def test_wf_list_routes_exact_configured_root(monkeypatch):
    captured = {}
    monkeypatch.setattr(server, "_repo_root", "/repo")

    def fake_list(root, *, limit):
        captured.update(root=root, limit=limit)
        return {
            "candidates": [
                {
                    "folder_id": "wf-abc123",
                    "path": "/repo/wf-abc123",
                }
            ]
        }

    monkeypatch.setattr(server._lifecycle, "do_list", fake_list)

    result = _run(server.wf_list(limit=5))

    assert captured == {"root": "/repo", "limit": 5}
    assert result == {"candidates": [{"folder_id": "wf-abc123"}]}


def test_fs_shells_route_exact_id_filename_fields(monkeypatch):
    tools = FakeFSTools()
    monkeypatch.setattr(server, "_fs_tools", tools)

    assert _run(server.fs_resolve("wf-abc123"))["filename"] == "_brief.md"
    _run(server.fs_stat("wf-abc123", "notes.md"))
    _run(server.fs_list("wf-abc123", "subdir"))
    _run(server.fs_read("wf-abc123", "notes.md", offset=2, limit=4))
    _run(server.fs_create("wf-abc123", "new.md", "content", idempotency_key="c"))
    _run(
        server.fs_write(
            "wf-abc123",
            "notes.md",
            "updated",
            expected_resource_revision="rev",
        )
    )
    _run(server.fs_edit("wf-abc123", "notes.md", "old", "new", replace_all=True))
    _run(server.fs_copy("wf-abc123", "a.md", "wf-def456", "b.md"))
    _run(server.fs_rename("wf-abc123", "a.md", "wf-def456", "b.md"))
    _run(server.fs_delete("wf-abc123", "old.md"))
    _run(
        server.fs_batch(
            [
                {
                    "op": "create",
                    "folder_id": "wf-abc123",
                    "filename": "batch.md",
                    "content": "x",
                }
            ]
        )
    )

    assert tools.calls[0] == ("fs_resolve", ("wf-abc123", "_brief.md"), {})
    assert tools.calls[3] == (
        "fs_read",
        ("wf-abc123", "notes.md"),
        {"offset": 2, "limit": 4},
    )
    assert tools.calls[7][1] == (
        "wf-abc123",
        "a.md",
        "wf-def456",
        "b.md",
    )
    assert all("path" not in _run(server.fs_capabilities()) for _ in [0])
