"""test_server_tools.py — WF-6 测试：6 个 fat tool shell + 边界硬化 + 工具注册验证。

TDD 顺序：先跑红，实现后转绿。
"""
import asyncio
import datetime
import inspect
import os

import katana_work_folder_mcp.server as server


# ---------------------------------------------------------------------------
# 辅助：固定 _wf_root / _store 为测试路径
# ---------------------------------------------------------------------------

def _set_wf_root(path: str) -> None:
    server._wf_root = path


def _set_store(store) -> None:
    server._store = store


# ---------------------------------------------------------------------------
# 1. _resolve_folder 测试
# ---------------------------------------------------------------------------

class TestResolveFolder:
    def test_absolute_path_returned_as_is(self):
        _set_wf_root("/kb/wf")
        result = server._resolve_folder("/absolute/path/to/folder")
        assert result == "/absolute/path/to/folder"

    def test_relative_joined_under_wf_root(self):
        _set_wf_root("/kb/wf")
        result = server._resolve_folder("2026/06/22/my-topic")
        assert result == "/kb/wf/2026/06/22/my-topic"

    def test_relative_with_none_wf_root_falls_back_to_dot(self):
        server._wf_root = None
        result = server._resolve_folder("some/topic")
        assert result == os.path.join(".", "some/topic")


# ---------------------------------------------------------------------------
# 2. _safe_resume_fields 测试
# ---------------------------------------------------------------------------

class TestSafeResumeFields:
    def test_none_returns_none(self):
        assert server._safe_resume_fields(None) is None

    def test_empty_dict_returns_none(self):
        assert server._safe_resume_fields({}) is None

    def test_keeps_allowed_keys(self):
        inp = {"goal": "my goal", "phase": "dev", "status": "active"}
        result = server._safe_resume_fields(inp)
        assert result == {"goal": "my goal", "phase": "dev", "status": "active"}

    def test_drops_unknown_keys(self):
        inp = {"goal": "x", "evil_key": "hacked", "__import__": "os"}
        result = server._safe_resume_fields(inp)
        assert result == {"goal": "x"}
        assert "evil_key" not in result
        assert "__import__" not in result

    def test_all_allowed_keys_pass(self):
        inp = {
            "goal": "g",
            "phase": "p",
            "status": "s",
            "wf_abs": "/a/b",
            "key_context": "ctx",
            "decisions": "d",
            "issues": "i",
            "lessons": "l",
            "now": "2026-06-22 13:00",
        }
        result = server._safe_resume_fields(inp)
        assert result == inp

    def test_mixed_known_and_unknown(self):
        inp = {"goal": "ok", "decisions": "yes", "bad_key": "no", "wf_abs": "/x"}
        result = server._safe_resume_fields(inp)
        assert set(result.keys()) == {"goal", "decisions", "wf_abs"}


# ---------------------------------------------------------------------------
# 3. 工具注册：6 个工具都已注册
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_all_six_tools_registered(self):
        tools = asyncio.run(server.mcp.list_tools())
        names = {t.name for t in tools}
        assert {"wf_search", "wf_create", "wf_list", "wf_save", "wf_resume", "wf_reindex"} <= names

    def test_server_module_has_all_tool_functions(self):
        """备用断言：确认模块属性层面 6 个异步函数都存在。"""
        for fn_name in ("wf_search", "wf_create", "wf_list", "wf_save", "wf_resume", "wf_reindex"):
            fn = getattr(server, fn_name, None)
            assert fn is not None, f"server.{fn_name} not found"
            assert inspect.iscoroutinefunction(fn), f"server.{fn_name} should be async"


# ---------------------------------------------------------------------------
# 4. 路由测试：wf_create 路由到 store.create
# ---------------------------------------------------------------------------

class TestWfCreateRouting:
    def test_routes_to_store_create_with_topic(self, monkeypatch):
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 13, 0, 0)

        class FakeStore:
            def create(self, topic, now_fn, expected_base_sha=None):
                captured["topic"] = topic
                captured["now"] = now_fn()
                captured["expected_base_sha"] = expected_base_sha
                return {"created": True, "path": "2026/06/22/test-topic",
                        "seeded": [], "id": "wf-000000",
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        result = asyncio.run(server.wf_create("test topic"))

        assert captured["topic"] == "test topic"
        assert captured["now"] == fake_now
        assert captured["expected_base_sha"] is None
        assert result["created"] is True

    def test_passes_expected_base_sha(self, monkeypatch):
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 13, 0, 0)

        class FakeStore:
            def create(self, topic, now_fn, expected_base_sha=None):
                captured["expected_base_sha"] = expected_base_sha
                return {"created": True, "path": "x", "seeded": [],
                        "id": "wf-000000",
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        asyncio.run(server.wf_create("topic", expected_base_sha="a" * 40))
        assert captured["expected_base_sha"] == "a" * 40


# ---------------------------------------------------------------------------
# 5. 路由测试：wf_resume 路由到 store.resume（含 _resolve_folder）
# ---------------------------------------------------------------------------

class TestWfResumeRouting:
    def test_routes_absolute_folder_to_store_resume(self, monkeypatch):
        _set_wf_root("/abs/path")
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 14, 0, 0)

        class FakeStore:
            def resume(self, folder, now_fn, probe_fn=None, expected_base_sha=None):
                captured["folder"] = folder
                captured["now"] = now_fn()
                captured["expected_base_sha"] = expected_base_sha
                return {"ok": True, "folder": folder, "blocked": False,
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        asyncio.run(server.wf_resume("/abs/path/to/folder"))

        assert captured["folder"] == "to/folder"
        assert captured["now"] == fake_now

    def test_resolves_relative_folder_against_wf_root(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}

        class FakeStore:
            def resume(self, folder, now_fn, probe_fn=None, expected_base_sha=None):
                captured["folder"] = folder
                return {"ok": True, "folder": folder, "blocked": False,
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        asyncio.run(server.wf_resume("2026/06/22/my-topic"))

        assert captured["folder"] == "2026/06/22/my-topic"


# ---------------------------------------------------------------------------
# 6. 路由测试：wf_list 路由到 _lifecycle.do_list
# ---------------------------------------------------------------------------

class TestWfListRouting:
    def test_routes_with_wf_root_and_limit(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}

        def fake_do_list(work_folder_root, *, limit):
            captured["root"] = work_folder_root
            captured["limit"] = limit
            return {"candidates": []}

        monkeypatch.setattr(server._lifecycle, "do_list", fake_do_list)
        result = asyncio.run(server.wf_list(limit=5))

        assert captured["root"] == "/kb/wf"
        assert captured["limit"] == 5
        assert result == {"candidates": []}


# ---------------------------------------------------------------------------
# 6b. 路由测试：wf_reindex 路由到 store.reindex
# ---------------------------------------------------------------------------

class TestWfReindexRouting:
    def test_registered_and_routes(self, monkeypatch):
        tools = asyncio.run(server.mcp.list_tools())
        assert "wf_reindex" in {t.name for t in tools}

        captured = {}

        class FakeStore:
            def reindex(self, dry_run=False, expected_base_sha=None):
                captured["dry_run"] = dry_run
                captured["expected_base_sha"] = expected_base_sha
                return {"indexed": 3, "skipped": 0, "errors": [],
                        "index_path": "INDEX.md",
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        result = asyncio.run(server.wf_reindex(dry_run=True))
        assert captured["dry_run"] is True
        assert result["indexed"] == 3

    def test_patches_index_path_to_absolute(self, monkeypatch):
        _set_wf_root("/kb/wf")

        class FakeStore:
            def reindex(self, dry_run=False, expected_base_sha=None):
                return {"indexed": 3, "skipped": 0, "errors": [],
                        "index_path": "INDEX.md",
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        result = asyncio.run(server.wf_reindex())
        assert result["index_path"] == "/kb/wf/INDEX.md"


# ---------------------------------------------------------------------------
# 7. 路由测试：wf_save 路由到 store.save（含 _safe_resume_fields 过滤）
# ---------------------------------------------------------------------------

class TestWfSaveRouting:
    def test_routes_with_filtered_resume_fields(self, monkeypatch):
        _set_wf_root("/abs/folder")
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 13, 30, 0)

        class FakeStore:
            def save(self, folder, now_fn, summary="checkpoint",
                     context_snapshot=None, resume_fields=None,
                     golden_order_additions=None, findings_addition=None,
                     expected_base_sha=None):
                captured["folder"] = folder
                captured["resume_fields"] = resume_fields
                captured["summary"] = summary
                return {"saved": True, "folder": folder, "written": [],
                        "contract": "x",
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        dirty_fields = {"goal": "ok", "evil": "drop_me"}
        asyncio.run(server.wf_save(
            "/abs/folder",
            summary="my checkpoint",
            resume_fields=dirty_fields,
        ))

        assert captured["summary"] == "my checkpoint"
        assert "evil" not in (captured["resume_fields"] or {})
        assert captured["resume_fields"].get("goal") == "ok"

    def test_none_resume_fields_passes_through(self, monkeypatch):
        _set_wf_root("/abs/folder")
        captured = {}

        class FakeStore:
            def save(self, folder, now_fn, summary="checkpoint",
                     context_snapshot=None, resume_fields=None,
                     golden_order_additions=None, findings_addition=None,
                     expected_base_sha=None):
                captured["resume_fields"] = resume_fields
                return {"saved": True, "folder": folder, "written": [],
                        "contract": "x",
                        "git": {"committed": True, "detail": "a" * 40},
                        "manifest": {"manifest_id": "x"}}

        _set_store(FakeStore())
        asyncio.run(server.wf_save("/abs/folder"))

        assert captured["resume_fields"] is None