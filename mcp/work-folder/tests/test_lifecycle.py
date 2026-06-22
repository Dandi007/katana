"""test_lifecycle.py — TDD 测试套件：lifecycle.py 的所有公开函数。

严格 TDD：本文件先于 lifecycle.py 存在。
测试覆盖：
  - slugify
  - do_create（创建 + 幂等第二次）
  - do_save（正常路径 / 缺少 folder）
  - do_resume MATCH / DRIFT / BROKEN / 缺文件
  - do_list
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

# 延迟 import，确保 lifecycle.py 存在才能通过
from katana_work_folder_mcp.lifecycle import (
    RESUME_BLOCKED_CONTRACT,
    RESUME_PROCEED_CONTRACT,
    SAVE_CONTRACT,
    do_create,
    do_list,
    do_resume,
    do_save,
    slugify,
)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _fixed_now():
    """固定时间：2026-06-22 09:00，用于所有需要 now_fn 的测试。"""
    return datetime(2026, 6, 22, 9, 0, 0)


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_spaces_to_dash(self):
        assert slugify("hello world") == "hello-world"

    def test_lowercase(self):
        assert slugify("FooBar") == "foobar"

    def test_collapse_double_dash(self):
        assert slugify("foo--bar") == "foo-bar"

    def test_punctuation_replaced(self):
        result = slugify("hello, world! test")
        assert result == "hello-world-test"

    def test_keeps_cjk(self):
        result = slugify("vault 搜索 service")
        assert "vault" in result
        assert "搜索" in result
        assert "service" in result
        # 应用连字符连接各段
        assert "-" in result

    def test_trim_leading_trailing_dash(self):
        result = slugify("  hello world  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_full_cjk(self):
        result = slugify("工作记录系统")
        assert result == "工作记录系统"

    def test_mixed(self):
        result = slugify("vault-search-service v2.0")
        # v2 和 0 都应保留；点被替换
        assert "vault-search-service" in result
        assert "v2" in result


# ---------------------------------------------------------------------------
# do_create
# ---------------------------------------------------------------------------

class TestDoCreate:
    def test_creates_folder_and_returns_created_true(self, tmp_path):
        result = do_create(str(tmp_path), "my topic", now_fn=_fixed_now)
        assert result["created"] is True
        path = Path(result["path"])
        assert path.exists()
        # 应在 2026/06/22/<slug>/ 下
        assert "2026" in str(path)
        assert "06" in str(path)
        assert "22" in str(path)

    def test_seeds_progress_and_context(self, tmp_path):
        result = do_create(str(tmp_path), "my topic", now_fn=_fixed_now)
        path = Path(result["path"])
        assert (path / "progress.md").exists()
        assert (path / "context.md").exists()
        # seeded 应至少包含这两个文件
        assert "progress.md" in result["seeded"]
        assert "context.md" in result["seeded"]

    def test_returns_drafting_contract(self, tmp_path):
        result = do_create(str(tmp_path), "my topic", now_fn=_fixed_now)
        assert result["drafting"] == SAVE_CONTRACT

    def test_second_call_returns_already_exists(self, tmp_path):
        do_create(str(tmp_path), "my topic", now_fn=_fixed_now)
        result2 = do_create(str(tmp_path), "my topic", now_fn=_fixed_now)
        assert result2["created"] is False
        assert result2["note"] == "已存在"
        assert Path(result2["path"]).exists()

    def test_path_uses_slug(self, tmp_path):
        result = do_create(str(tmp_path), "Vault 搜索 Service", now_fn=_fixed_now)
        folder_name = Path(result["path"]).name
        # slug 应包含小写英文 + CJK，连字符连接
        assert "vault" in folder_name
        assert "service" in folder_name


# ---------------------------------------------------------------------------
# do_save
# ---------------------------------------------------------------------------

class TestDoSave:
    def _make_folder(self, tmp_path) -> str:
        result = do_create(str(tmp_path), "test topic", now_fn=_fixed_now)
        return result["path"]

    def test_changelog_row_count_increases(self, tmp_path):
        folder = self._make_folder(tmp_path)
        progress_before = (Path(folder) / "progress.md").read_text()
        # 计算初始 changelog 行数（跳过表头 + 分隔符）
        rows_before = [l for l in progress_before.splitlines()
                       if l.startswith("| ") and "Action" not in l and "---" not in l]

        do_save(folder, now_fn=_fixed_now, summary="first save")
        progress_after = (Path(folder) / "progress.md").read_text()
        rows_after = [l for l in progress_after.splitlines()
                      if l.startswith("| ") and "Action" not in l and "---" not in l]

        assert len(rows_after) == len(rows_before) + 1

    def test_claude_md_and_agents_md_exist_and_identical(self, tmp_path):
        folder = self._make_folder(tmp_path)
        do_save(folder, now_fn=_fixed_now)
        claude = (Path(folder) / "CLAUDE.md").read_text()
        agents = (Path(folder) / "AGENTS.md").read_text()
        assert claude == agents
        assert len(claude) > 0

    def test_context_snapshot_overwritten(self, tmp_path):
        folder = self._make_folder(tmp_path)
        snapshot = "# Context\n\n新快照内容\n"
        do_save(folder, now_fn=_fixed_now, context_snapshot=snapshot)
        content = (Path(folder) / "context.md").read_text()
        assert content == snapshot

    def test_golden_order_appended(self, tmp_path):
        folder = self._make_folder(tmp_path)
        do_save(folder, now_fn=_fixed_now, golden_order_additions="- 用户选择方案A\n")
        go = (Path(folder) / "golden-order.md").read_text()
        assert "用户选择方案A" in go

    def test_golden_order_append_accumulates(self, tmp_path):
        folder = self._make_folder(tmp_path)
        do_save(folder, now_fn=_fixed_now, golden_order_additions="- 第一条\n")
        do_save(folder, now_fn=_fixed_now, golden_order_additions="- 第二条\n")
        go = (Path(folder) / "golden-order.md").read_text()
        assert "第一条" in go
        assert "第二条" in go

    def test_findings_appended(self, tmp_path):
        folder = self._make_folder(tmp_path)
        do_save(folder, now_fn=_fixed_now, findings_addition="## 关键发现\n踩坑：X\n")
        findings = (Path(folder) / "findings.md").read_text()
        assert "踩坑：X" in findings

    def test_returns_written_list_and_contract(self, tmp_path):
        folder = self._make_folder(tmp_path)
        result = do_save(folder, now_fn=_fixed_now)
        assert result["saved"] is True
        assert result["folder"] == str(Path(folder).resolve())
        assert isinstance(result["written"], list)
        assert len(result["written"]) >= 1  # 至少 progress.md + CLAUDE.md + AGENTS.md
        assert result["contract"] == SAVE_CONTRACT

    def test_missing_folder_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            do_save(str(tmp_path / "does_not_exist"), now_fn=_fixed_now)


# ---------------------------------------------------------------------------
# do_resume — fake probe_fn 工厂
# ---------------------------------------------------------------------------

def _make_probe(*, exists=True, is_git=True, branch="feat/wf-mcp", dirty=False):
    """创建返回固定 fact 的假探针。"""
    def probe(path: str) -> dict:
        return {"exists": exists, "is_git": is_git, "branch": branch, "dirty": dirty}
    return probe


_CONTEXT_WITH_PATH = """\
# Context

**Updated:** 2026-06-22 09:00

## 工作上下文
- 测试工作文件夹

## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| wf-mcp 工作树 | /Volumes/Data/code/worktrees/katana/wf-mcp | feat/wf-mcp | 主工作树 |

## 环境信息
- Python 3.12
"""


class TestDoResume:
    def _make_folder(self, tmp_path, *, with_progress=True, with_claude=False,
                     context_md: str | None = None) -> str:
        folder = str(tmp_path / "wf")
        os.makedirs(folder, exist_ok=True)
        if with_progress:
            (Path(folder) / "progress.md").write_text(
                "# Progress\n\n**Goal:** test\n**Status:** active\n**Phase:** impl\n"
                "**Updated:** 2026-06-22\n\n"
                "## Changelog\n| Time | Action | Detail |\n|------|--------|--------|\n",
                encoding="utf-8",
            )
        if with_claude:
            (Path(folder) / "CLAUDE.md").write_text("# Resume Guide\n\n", encoding="utf-8")
        ctx = context_md if context_md is not None else _CONTEXT_WITH_PATH
        (Path(folder) / "context.md").write_text(ctx, encoding="utf-8")
        return folder

    # --- MATCH ---
    def test_match_overall_not_blocked(self, tmp_path):
        folder = self._make_folder(tmp_path)
        probe = _make_probe(exists=True, is_git=True, branch="feat/wf-mcp", dirty=False)
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        assert result["ok"] is True
        assert result["verification"]["overall"] == "MATCH"
        assert result["blocked"] is False
        assert result["contract"] == RESUME_PROCEED_CONTRACT

    def test_match_changelog_has_resume_row(self, tmp_path):
        folder = self._make_folder(tmp_path)
        probe = _make_probe(exists=True, is_git=True, branch="feat/wf-mcp", dirty=False)
        do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        progress = (Path(folder) / "progress.md").read_text()
        assert "resume" in progress

    # --- DRIFT ---
    def test_drift_overall_not_blocked(self, tmp_path):
        folder = self._make_folder(tmp_path)
        # 分支不一致 → DRIFT
        probe = _make_probe(exists=True, is_git=True, branch="main", dirty=False)
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        assert result["ok"] is True
        assert result["verification"]["overall"] == "DRIFT"
        assert result["blocked"] is False
        assert result["contract"] == RESUME_PROCEED_CONTRACT

    # --- BROKEN ---
    def test_broken_overall_blocked_true(self, tmp_path):
        folder = self._make_folder(tmp_path)
        # 路径不存在 → BROKEN
        probe = _make_probe(exists=False)
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        assert result["ok"] is True
        assert result["verification"]["overall"] == "BROKEN"
        assert result["blocked"] is True

    def test_broken_contract_is_blocked_contract(self, tmp_path):
        folder = self._make_folder(tmp_path)
        probe = _make_probe(exists=False)
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        assert result["contract"] == RESUME_BLOCKED_CONTRACT

    def test_broken_resume_report_mentions_path(self, tmp_path):
        folder = self._make_folder(tmp_path)
        probe = _make_probe(exists=False)
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        # resume_report 应包含 broken 路径
        assert "/Volumes/Data/code/worktrees/katana/wf-mcp" in result["resume_report"]

    def test_broken_with_empty_context_no_resources(self, tmp_path):
        """context.md 无关键路径表时：0 资源 → overall MATCH，不 blocked。"""
        folder = self._make_folder(tmp_path, context_md="# Context\n\n## 工作上下文\n- 暂无\n")
        probe = _make_probe(exists=False)  # 探针返回值无关，因为 0 资源
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        assert result["ok"] is True
        assert result["blocked"] is False

    # --- 缺文件 → ok False ---
    def test_missing_progress_and_claude_returns_ok_false(self, tmp_path):
        folder = str(tmp_path / "empty")
        os.makedirs(folder)
        # 不写 progress.md / CLAUDE.md
        result = do_resume(folder, now_fn=_fixed_now)
        assert result["ok"] is False
        assert result["blocked"] is True
        assert "error" in result

    def test_missing_folder_returns_ok_false(self, tmp_path):
        result = do_resume(str(tmp_path / "no_such"), now_fn=_fixed_now)
        assert result["ok"] is False
        assert result["blocked"] is True

    # --- loaded 结构 ---
    def test_loaded_keys_present(self, tmp_path):
        folder = self._make_folder(tmp_path)
        probe = _make_probe()
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        assert "loaded" in result
        loaded = result["loaded"]
        # 至少包含已有文件的 key
        assert "progress" in loaded
        assert "context" in loaded

    # --- verification 结构 ---
    def test_verdicts_list_in_verification(self, tmp_path):
        folder = self._make_folder(tmp_path)
        probe = _make_probe()
        result = do_resume(folder, now_fn=_fixed_now, probe_fn=probe)
        v = result["verification"]
        assert "overall" in v
        assert "verdicts" in v
        assert isinstance(v["verdicts"], list)
        # 有一条资源
        assert len(v["verdicts"]) == 1
        vd = v["verdicts"][0]
        assert "level" in vd
        assert "detail" in vd


# ---------------------------------------------------------------------------
# do_list
# ---------------------------------------------------------------------------

class TestDoList:
    def test_returns_candidates(self, tmp_path):
        # 建两个 active + 一个 completed
        for name in ("topic-a", "topic-b"):
            folder = tmp_path / "2026" / "06" / "22" / name
            folder.mkdir(parents=True)
            (folder / "progress.md").write_text(
                "# Progress\n**Status:** active\n## Changelog\n| Time | Action | Detail |\n|------|--------|--------|\n",
                encoding="utf-8",
            )

        completed = tmp_path / "2026" / "06" / "21" / "done-topic"
        completed.mkdir(parents=True)
        (completed / "progress.md").write_text(
            "# Progress\n**Status:** completed\n## Changelog\n| Time | Action | Detail |\n|------|--------|--------|\n",
            encoding="utf-8",
        )

        result = do_list(str(tmp_path))
        assert "candidates" in result
        # completed 应被过滤
        paths = [c["path"] for c in result["candidates"]]
        assert all("done-topic" not in p for p in paths)
        assert len(result["candidates"]) == 2

    def test_limit_respected(self, tmp_path):
        for i in range(5):
            folder = tmp_path / "2026" / "06" / "22" / f"topic-{i}"
            folder.mkdir(parents=True)
            (folder / "progress.md").write_text(
                "# Progress\n**Status:** active\n## Changelog\n| Time | Action | Detail |\n|------|--------|--------|\n",
                encoding="utf-8",
            )
        result = do_list(str(tmp_path), limit=3)
        assert len(result["candidates"]) <= 3

    def test_empty_root(self, tmp_path):
        result = do_list(str(tmp_path / "no_such"))
        assert result["candidates"] == []
