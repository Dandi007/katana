"""test_server_tools.py — WF-5 测试：4 个 fat tool shell + 边界硬化 + 工具注册验证。

TDD 顺序：先跑红，实现后转绿。
"""
import asyncio
import datetime
import inspect

import katana_work_folder_mcp.server as server


# ---------------------------------------------------------------------------
# 辅助：固定 _wf_root 为测试路径
# ---------------------------------------------------------------------------

def _set_wf_root(path: str) -> None:
    server._wf_root = path


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
        import os
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
# 3. 工具注册：5 个工具都已注册
# ---------------------------------------------------------------------------

class TestToolRegistration:
    def test_all_five_tools_registered(self):
        tools = asyncio.run(server.mcp.list_tools())
        names = {t.name for t in tools}
        assert {"wf_search", "wf_create", "wf_list", "wf_save", "wf_resume"} <= names

    def test_server_module_has_all_tool_functions(self):
        """备用断言：确认模块属性层面 5 个异步函数都存在。"""
        for fn_name in ("wf_search", "wf_create", "wf_list", "wf_save", "wf_resume"):
            fn = getattr(server, fn_name, None)
            assert fn is not None, f"server.{fn_name} not found"
            assert inspect.iscoroutinefunction(fn), f"server.{fn_name} should be async"


# ---------------------------------------------------------------------------
# 4. 路由测试：wf_create 路由到 _lifecycle.do_create
# ---------------------------------------------------------------------------

class TestWfCreateRouting:
    def test_routes_to_do_create_with_wf_root_and_topic(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 13, 0, 0)

        def fake_do_create(work_folder_root, topic, *, now_fn):
            captured["work_folder_root"] = work_folder_root
            captured["topic"] = topic
            captured["now"] = now_fn()
            return {"created": True, "path": "/kb/wf/2026/06/22/test-topic", "seeded": []}

        monkeypatch.setattr(server._lifecycle, "do_create", fake_do_create)
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        result = asyncio.run(server.wf_create("test topic"))

        assert captured["work_folder_root"] == "/kb/wf"
        assert captured["topic"] == "test topic"
        assert captured["now"] == fake_now
        assert result["created"] is True

    def test_uses_dot_when_wf_root_none(self, monkeypatch):
        server._wf_root = None
        captured = {}

        def fake_do_create(work_folder_root, topic, *, now_fn):
            captured["work_folder_root"] = work_folder_root
            return {"created": True, "path": "/x", "seeded": []}

        monkeypatch.setattr(server._lifecycle, "do_create", fake_do_create)
        asyncio.run(server.wf_create("topic"))
        assert captured["work_folder_root"] == "."


# ---------------------------------------------------------------------------
# 5. 路由测试：wf_resume 路由到 _lifecycle.do_resume（含 _resolve_folder）
# ---------------------------------------------------------------------------

class TestWfResumeRouting:
    def test_routes_absolute_folder_to_do_resume(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 14, 0, 0)

        def fake_do_resume(folder, *, now_fn, **kwargs):
            captured["folder"] = folder
            captured["now"] = now_fn()
            return {"ok": True, "folder": folder, "blocked": False}

        monkeypatch.setattr(server._lifecycle, "do_resume", fake_do_resume)
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        asyncio.run(server.wf_resume("/abs/path/to/folder"))

        assert captured["folder"] == "/abs/path/to/folder"
        assert captured["now"] == fake_now

    def test_resolves_relative_folder_against_wf_root(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}

        def fake_do_resume(folder, *, now_fn, **kwargs):
            captured["folder"] = folder
            return {"ok": True, "folder": folder, "blocked": False}

        monkeypatch.setattr(server._lifecycle, "do_resume", fake_do_resume)
        asyncio.run(server.wf_resume("2026/06/22/my-topic"))

        assert captured["folder"] == "/kb/wf/2026/06/22/my-topic"


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
# 6b. 路由测试：wf_reindex 路由到 _reindex.reindex（用 _wf_root）
# ---------------------------------------------------------------------------

class TestWfReindexRouting:
    def test_registered_and_routes(self, monkeypatch):
        tools = asyncio.run(server.mcp.list_tools())
        assert "wf_reindex" in {t.name for t in tools}

        _set_wf_root("/kb/wf")
        captured = {}

        def fake_reindex(root, dry_run=False):
            captured["root"] = root
            captured["dry_run"] = dry_run
            return {"indexed": 3, "skipped": 0, "errors": [], "index_path": "/kb/wf/INDEX.md"}

        monkeypatch.setattr(server._reindex, "reindex", fake_reindex)
        result = asyncio.run(server.wf_reindex(dry_run=True))
        assert captured["root"] == "/kb/wf"
        assert captured["dry_run"] is True
        assert result["indexed"] == 3


# ---------------------------------------------------------------------------
# 7. 路由测试：wf_save 路由到 _lifecycle.do_save（含 _safe_resume_fields 过滤）
# ---------------------------------------------------------------------------

class TestWfSaveRouting:
    def test_routes_with_resolved_folder_and_filtered_resume_fields(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}
        fake_now = datetime.datetime(2026, 6, 22, 13, 30, 0)

        def fake_do_save(folder, *, now_fn, summary, context_snapshot,
                         resume_fields, golden_order_additions, findings_addition):
            captured["folder"] = folder
            captured["resume_fields"] = resume_fields
            captured["summary"] = summary
            return {"saved": True, "folder": folder, "written": [], "contract": "x"}

        monkeypatch.setattr(server._lifecycle, "do_save", fake_do_save)
        monkeypatch.setattr(server, "_now", lambda: fake_now)

        dirty_fields = {"goal": "ok", "evil": "drop_me"}
        asyncio.run(server.wf_save(
            "/abs/folder",
            summary="my checkpoint",
            resume_fields=dirty_fields,
        ))

        assert captured["folder"] == "/abs/folder"
        assert captured["summary"] == "my checkpoint"
        # evil key must be filtered out
        assert "evil" not in (captured["resume_fields"] or {})
        assert captured["resume_fields"].get("goal") == "ok"

    def test_none_resume_fields_passes_through(self, monkeypatch):
        _set_wf_root("/kb/wf")
        captured = {}

        def fake_do_save(folder, *, now_fn, summary, context_snapshot,
                         resume_fields, golden_order_additions, findings_addition):
            captured["resume_fields"] = resume_fields
            return {"saved": True, "folder": folder, "written": [], "contract": "x"}

        monkeypatch.setattr(server._lifecycle, "do_save", fake_do_save)
        asyncio.run(server.wf_save("/abs/folder"))

        assert captured["resume_fields"] is None
