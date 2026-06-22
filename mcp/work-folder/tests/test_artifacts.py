"""test_artifacts.py — TDD tests for artifacts.py (work-folder artifact I/O layer).

纯函数测试直接调用；IO 函数用 tmp_path fixture。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import katana_work_folder_mcp.artifacts as art


# ---------------------------------------------------------------------------
# render_progress_skeleton
# ---------------------------------------------------------------------------

class TestRenderProgressSkeleton:
    def _render(self, **kw):
        defaults = dict(goal="测试目标", status="execution", phase="阶段一", now="2026-06-22 13:00")
        defaults.update(kw)
        return art.render_progress_skeleton(**defaults)

    def test_contains_goal(self):
        out = self._render(goal="My Goal")
        assert "My Goal" in out

    def test_contains_status(self):
        out = self._render(status="brainstorming")
        assert "brainstorming" in out

    def test_contains_phase(self):
        out = self._render(phase="Phase X")
        assert "Phase X" in out

    def test_contains_updated(self):
        out = self._render(now="2026-06-22 13:00")
        assert "2026-06-22 13:00" in out

    def test_section_headers(self):
        out = self._render()
        for header in ("## Completed", "## Current", "## Blocked", "## Next", "## Changelog"):
            assert header in out, f"Missing header: {header}"

    def test_blocked_none(self):
        out = self._render()
        assert "- None" in out

    def test_changelog_table_header(self):
        out = self._render()
        assert "| Time | Action | Detail |" in out

    def test_changelog_table_separator(self):
        out = self._render()
        assert "|------|" in out or "|---" in out

    def test_no_data_rows(self):
        """Changelog 初始模板不应有任何数据行（只有表头+分隔符）。"""
        out = self._render()
        lines = out.splitlines()
        in_changelog = False
        data_rows = []
        for line in lines:
            if line.strip() == "## Changelog":
                in_changelog = True
                continue
            if in_changelog and line.startswith("## "):
                break
            if in_changelog and line.startswith("|") and "Time" not in line and "---" not in line:
                data_rows.append(line)
        assert data_rows == [], f"Unexpected data rows: {data_rows}"

    def test_progress_title(self):
        out = self._render()
        assert "# Progress" in out

    def test_field_labels(self):
        out = self._render()
        assert "**Goal:**" in out
        assert "**Status:**" in out
        assert "**Phase:**" in out
        assert "**Updated:**" in out


# ---------------------------------------------------------------------------
# render_context_skeleton
# ---------------------------------------------------------------------------

class TestRenderContextSkeleton:
    def _render(self, **kw):
        defaults = dict(now="2026-06-22 13:00")
        defaults.update(kw)
        return art.render_context_skeleton(**defaults)

    def test_updated_field(self):
        out = self._render(now="2026-06-22 13:00")
        assert "**Updated:**" in out
        assert "2026-06-22 13:00" in out

    def test_work_context_section(self):
        out = self._render()
        assert "## 工作上下文" in out

    def test_key_path_section(self):
        out = self._render()
        assert "## 关键路径" in out

    def test_key_path_table_header(self):
        out = self._render()
        assert "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |" in out

    def test_env_section(self):
        out = self._render()
        assert "## 环境信息" in out

    def test_context_title(self):
        out = self._render()
        assert "# Context" in out


# ---------------------------------------------------------------------------
# render_resume_guide
# ---------------------------------------------------------------------------

class TestRenderResumeGuide:
    def _render(self, **kw):
        defaults = dict(
            goal="测试目标",
            phase="阶段一",
            status="execution",
            wf_abs="/some/path/to/wf",
            key_context="关键路径",
            decisions="",
            issues="",
            lessons="",
            now="2026-06-22 13:00",
        )
        defaults.update(kw)
        return art.render_resume_guide(**defaults)

    def test_resume_guide_title(self):
        out = self._render()
        assert "# Resume Guide" in out

    def test_updated_timestamp(self):
        out = self._render(now="2026-06-22 13:00")
        assert "2026-06-22 13:00" in out

    def test_goal_section(self):
        out = self._render(goal="My Goal")
        assert "## Goal" in out
        assert "My Goal" in out

    def test_status_section(self):
        out = self._render()
        assert "## Status" in out

    def test_phase_in_status(self):
        out = self._render(phase="Phase Y")
        assert "Phase Y" in out

    def test_wf_abs_in_output(self):
        out = self._render(wf_abs="/abs/path/wf")
        assert "/abs/path/wf" in out

    def test_key_context_section(self):
        out = self._render(key_context="some context")
        assert "## Key Context" in out
        assert "some context" in out

    def test_key_decisions_section(self):
        out = self._render(decisions="some decision")
        assert "## Key Decisions" in out
        assert "some decision" in out

    def test_known_issues_section(self):
        out = self._render(issues="some issue")
        assert "## Known Issues" in out
        assert "some issue" in out

    def test_lessons_section(self):
        out = self._render(lessons="some lesson")
        assert "## Lessons" in out
        assert "some lesson" in out

    def test_empty_decisions_defaults_to_zanwu(self):
        out = self._render(decisions="")
        assert "暂无" in out

    def test_empty_issues_defaults_to_zanwu(self):
        out = self._render(issues="")
        assert "暂无" in out

    def test_empty_lessons_defaults_to_zanwu(self):
        out = self._render(lessons="")
        assert "暂无" in out

    def test_resume_steps_section(self):
        out = self._render()
        assert "## Resume Steps" in out

    def test_four_resume_steps(self):
        out = self._render()
        # 至少包含 4 个带编号的步骤
        steps = [line for line in out.splitlines() if line.startswith(("1.", "2.", "3.", "4."))]
        assert len(steps) >= 4


# ---------------------------------------------------------------------------
# changelog_row
# ---------------------------------------------------------------------------

class TestChangelogRow:
    def test_basic(self):
        row = art.changelog_row("13:00", "checkpoint", "初始化")
        assert row == "| 13:00 | checkpoint | 初始化 |"

    def test_format(self):
        row = art.changelog_row("T", "A", "D")
        assert row.startswith("| T |")
        assert row.endswith("| D |")


# ---------------------------------------------------------------------------
# insert_changelog_row
# ---------------------------------------------------------------------------

class TestInsertChangelogRow:
    SAMPLE_PROGRESS = """\
# Progress

**Goal:** test
**Status:** execution
**Phase:** p1
**Updated:** 2026-06-22 13:00

## Completed
- done

## Current
- working

## Blocked
- None

## Next
- next

## Changelog
| Time | Action | Detail |
|------|--------|--------|
"""

    def test_append_row(self):
        row = "| 13:01 | checkpoint | 完成 |"
        out = art.insert_changelog_row(self.SAMPLE_PROGRESS, row)
        assert row in out

    def test_idempotent(self):
        row = "| 13:01 | checkpoint | 完成 |"
        out1 = art.insert_changelog_row(self.SAMPLE_PROGRESS, row)
        out2 = art.insert_changelog_row(out1, row)
        # 只出现一次
        assert out2.count(row) == 1

    def test_row_position_after_separator(self):
        """新行应在表格分隔符之后。"""
        row = "| 13:01 | checkpoint | 完成 |"
        out = art.insert_changelog_row(self.SAMPLE_PROGRESS, row)
        lines = out.splitlines()
        sep_idx = next(i for i, l in enumerate(lines) if "|---" in l)
        row_idx = next(i for i, l in enumerate(lines) if row in l)
        assert row_idx > sep_idx

    def test_no_table_appends_section(self):
        """没有 Changelog 表格时，应追加完整 Changelog section。"""
        bare = "# Progress\n\n**Status:** execution\n"
        row = "| 13:01 | checkpoint | 完成 |"
        out = art.insert_changelog_row(bare, row)
        assert "## Changelog" in out
        assert "| Time | Action | Detail |" in out
        assert row in out

    def test_existing_rows_preserved(self):
        """已有数据行不被删除。"""
        existing_row = "| 12:00 | init | 初始化 |"
        md_with_row = self.SAMPLE_PROGRESS + existing_row + "\n"
        new_row = "| 13:01 | checkpoint | 完成 |"
        out = art.insert_changelog_row(md_with_row, new_row)
        assert existing_row in out
        assert new_row in out


# ---------------------------------------------------------------------------
# parse_status
# ---------------------------------------------------------------------------

class TestParseStatus:
    def test_extracts_execution(self):
        md = "# Progress\n\n**Status:** execution\n"
        assert art.parse_status(md) == "execution"

    def test_extracts_brainstorming(self):
        md = "**Status:** brainstorming\n"
        assert art.parse_status(md) == "brainstorming"

    def test_extracts_completed(self):
        md = "**Status:** completed\n"
        assert art.parse_status(md) == "completed"

    def test_absent_returns_empty(self):
        md = "# Progress\n\nno status here\n"
        assert art.parse_status(md) == ""

    def test_strips_whitespace(self):
        md = "**Status:**  execution  \n"
        assert art.parse_status(md) == "execution"


# ---------------------------------------------------------------------------
# IO: read_artifact / write_artifact
# ---------------------------------------------------------------------------

class TestReadWriteArtifact:
    def test_write_and_read(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.write_artifact(folder, "progress.md", "hello world")
        content = art.read_artifact(folder, "progress.md")
        assert content == "hello world"

    def test_read_missing_returns_none(self, tmp_path):
        folder = str(tmp_path / "wf")
        assert art.read_artifact(folder, "missing.md") is None

    def test_write_creates_parents(self, tmp_path):
        folder = str(tmp_path / "deep" / "nested" / "wf")
        art.write_artifact(folder, "x.md", "content")
        assert (Path(folder) / "x.md").read_text(encoding="utf-8") == "content"


# ---------------------------------------------------------------------------
# IO: ensure_folder
# ---------------------------------------------------------------------------

class TestEnsureFolder:
    def test_creates_progress_and_context(self, tmp_path):
        folder = str(tmp_path / "wf")
        created = art.ensure_folder(folder, goal="G", status="brainstorming", phase="P1", now="2026-06-22 13:00")
        assert set(created) == {"progress.md", "context.md"}
        assert (Path(folder) / "progress.md").exists()
        assert (Path(folder) / "context.md").exists()

    def test_second_call_returns_empty(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.ensure_folder(folder, goal="G", status="brainstorming", phase="P1", now="2026-06-22 13:00")
        created = art.ensure_folder(folder, goal="G", status="brainstorming", phase="P1", now="2026-06-22 13:00")
        assert created == []

    def test_does_not_overwrite_existing_progress(self, tmp_path):
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        sentinel = "# Sentinel\nDo not overwrite!\n"
        (Path(folder) / "progress.md").write_text(sentinel, encoding="utf-8")
        created = art.ensure_folder(folder, goal="G", status="execution", phase="P1", now="2026-06-22 13:00")
        assert "progress.md" not in created
        assert (Path(folder) / "progress.md").read_text(encoding="utf-8") == sentinel

    def test_seeds_only_missing_file(self, tmp_path):
        """progress.md 已存在，只创建 context.md。"""
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        (Path(folder) / "progress.md").write_text("existing", encoding="utf-8")
        created = art.ensure_folder(folder, goal="G", now="2026-06-22 13:00")
        assert created == ["context.md"]

    def test_goal_reflected_in_progress(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.ensure_folder(folder, goal="My Special Goal", now="2026-06-22 13:00")
        content = (Path(folder) / "progress.md").read_text(encoding="utf-8")
        assert "My Special Goal" in content


# ---------------------------------------------------------------------------
# IO: append_changelog
# ---------------------------------------------------------------------------

class TestAppendChangelog:
    def test_adds_row(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.ensure_folder(folder, goal="G", now="2026-06-22 13:00")
        result = art.append_changelog(folder, time="13:01", action="checkpoint", detail="完成")
        assert result is True
        content = (Path(folder) / "progress.md").read_text(encoding="utf-8")
        assert "| 13:01 | checkpoint | 完成 |" in content

    def test_idempotent_returns_false(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.ensure_folder(folder, goal="G", now="2026-06-22 13:00")
        art.append_changelog(folder, time="13:01", action="checkpoint", detail="完成")
        result = art.append_changelog(folder, time="13:01", action="checkpoint", detail="完成")
        assert result is False

    def test_idempotent_single_occurrence(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.ensure_folder(folder, goal="G", now="2026-06-22 13:00")
        art.append_changelog(folder, time="13:01", action="checkpoint", detail="完成")
        art.append_changelog(folder, time="13:01", action="checkpoint", detail="完成")
        content = (Path(folder) / "progress.md").read_text(encoding="utf-8")
        assert content.count("| 13:01 | checkpoint | 完成 |") == 1

    def test_seeds_progress_if_missing(self, tmp_path):
        """progress.md 不存在时应先 seed 再追加。"""
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        result = art.append_changelog(folder, time="13:01", action="init", detail="seed")
        assert result is True
        content = (Path(folder) / "progress.md").read_text(encoding="utf-8")
        assert "| 13:01 | init | seed |" in content


# ---------------------------------------------------------------------------
# IO: write_context_snapshot
# ---------------------------------------------------------------------------

class TestWriteContextSnapshot:
    def test_overwrites(self, tmp_path):
        folder = str(tmp_path / "wf")
        art.ensure_folder(folder, goal="G", now="2026-06-22 13:00")
        art.write_context_snapshot(folder, "# Context\n\nnew content\n")
        content = art.read_artifact(folder, "context.md")
        assert content == "# Context\n\nnew content\n"

    def test_creates_if_missing(self, tmp_path):
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        art.write_context_snapshot(folder, "snapshot content")
        assert art.read_artifact(folder, "context.md") == "snapshot content"


# ---------------------------------------------------------------------------
# IO: gen_resume_guide
# ---------------------------------------------------------------------------

class TestGenResumeGuide:
    def test_writes_claude_and_agents(self, tmp_path):
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        created = art.gen_resume_guide(
            folder,
            goal="G",
            phase="P",
            status="execution",
            wf_abs=folder,
            now="2026-06-22 13:00",
        )
        assert set(created) == {"CLAUDE.md", "AGENTS.md"}

    def test_identical_content(self, tmp_path):
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        art.gen_resume_guide(
            folder,
            goal="G",
            phase="P",
            status="execution",
            wf_abs=folder,
            now="2026-06-22 13:00",
        )
        claude_content = (Path(folder) / "CLAUDE.md").read_text(encoding="utf-8")
        agents_content = (Path(folder) / "AGENTS.md").read_text(encoding="utf-8")
        assert claude_content == agents_content

    def test_contains_resume_guide(self, tmp_path):
        folder = str(tmp_path / "wf")
        Path(folder).mkdir(parents=True)
        art.gen_resume_guide(
            folder,
            goal="Special Goal",
            phase="P",
            status="execution",
            wf_abs=folder,
            now="2026-06-22 13:00",
        )
        content = (Path(folder) / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# Resume Guide" in content
        assert "Special Goal" in content


# ---------------------------------------------------------------------------
# IO: list_work_folders
# ---------------------------------------------------------------------------

class TestListWorkFolders:
    def _make_wf(self, base: Path, name: str, status: str, delay: float = 0) -> str:
        """在 base/name 创建含 progress.md 的工作目录。"""
        if delay:
            time.sleep(delay)
        wf = base / name
        wf.mkdir(parents=True)
        progress = f"# Progress\n\n**Status:** {status}\n"
        (wf / "progress.md").write_text(progress, encoding="utf-8")
        return str(wf)

    def test_excludes_completed(self, tmp_path):
        self._make_wf(tmp_path, "wf-done", "completed")
        self._make_wf(tmp_path, "wf-active", "execution", delay=0.01)
        results = art.list_work_folders(str(tmp_path))
        paths = [r["path"] for r in results]
        assert not any("wf-done" in p for p in paths)
        assert any("wf-active" in p for p in paths)

    def test_returns_two_active(self, tmp_path):
        self._make_wf(tmp_path, "wf-a", "execution")
        self._make_wf(tmp_path, "wf-b", "brainstorming", delay=0.01)
        self._make_wf(tmp_path, "wf-done", "completed")
        results = art.list_work_folders(str(tmp_path))
        assert len(results) == 2

    def test_sorted_by_mtime_desc(self, tmp_path):
        self._make_wf(tmp_path, "wf-old", "execution")
        time.sleep(0.02)
        self._make_wf(tmp_path, "wf-new", "brainstorming")
        results = art.list_work_folders(str(tmp_path))
        assert "wf-new" in results[0]["path"]
        assert "wf-old" in results[1]["path"]

    def test_result_schema(self, tmp_path):
        self._make_wf(tmp_path, "wf-x", "execution")
        results = art.list_work_folders(str(tmp_path))
        assert len(results) == 1
        r = results[0]
        assert "path" in r
        assert "status" in r
        assert "mtime" in r
        assert r["status"] == "execution"
        assert isinstance(r["mtime"], float)

    def test_includes_folder_with_only_claude_md(self, tmp_path):
        """含 CLAUDE.md 但无 progress.md 的目录也应被列出。"""
        wf = tmp_path / "wf-agent-only"
        wf.mkdir()
        (wf / "CLAUDE.md").write_text("# Resume Guide\n", encoding="utf-8")
        results = art.list_work_folders(str(tmp_path))
        assert len(results) == 1
        assert results[0]["status"] == ""

    def test_empty_root(self, tmp_path):
        results = art.list_work_folders(str(tmp_path))
        assert results == []

    def test_finds_date_nested_folders(self, tmp_path):
        """真实布局 YYYY/MM/DD/<topic>/ 深嵌，必须递归命中（非 root 直接子目录）。"""
        self._make_wf(tmp_path, "2026/06/21/topic-a", "execution")
        self._make_wf(tmp_path, "2026/06/22/topic-b", "brainstorming", delay=0.01)
        results = art.list_work_folders(str(tmp_path))
        paths = [r["path"] for r in results]
        assert len(results) == 2
        assert any(p.endswith("topic-a") for p in paths)
        assert any(p.endswith("topic-b") for p in paths)

    def test_prunes_at_work_folder_leaf(self, tmp_path):
        """命中 work-folder 后不再下钻：嵌套子目录里的 progress.md 不被当成独立条目。"""
        outer = self._make_wf(tmp_path, "2026/06/21/outer", "execution")
        nested = Path(outer) / "subdir"
        nested.mkdir()
        (nested / "progress.md").write_text("# Progress\n\n**Status:** execution\n", encoding="utf-8")
        results = art.list_work_folders(str(tmp_path))
        assert len(results) == 1
        assert results[0]["path"].endswith("outer")
