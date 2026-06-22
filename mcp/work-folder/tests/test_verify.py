"""test_verify.py — verify.py 的测试套件（TDD：先写测试，再实现）。"""
from __future__ import annotations

import pytest

# 测试目标：mcp/work-folder/katana_work_folder_mcp/verify.py
from katana_work_folder_mcp.verify import (
    MATCH, DRIFT, BROKEN,
    Resource, ResourceVerdict,
    parse_context_paths,
    classify,
    verify_env,
    overall_level,
    fs_git_probe,
)

# ---------------------------------------------------------------------------
# 测试数据：带关键路径表的 context.md 示例
# ---------------------------------------------------------------------------

CONTEXT_WITH_TABLE = """\
# Context

**Updated:** 2026-06-21 10:00

## 工作上下文
- 正在开发 work-folder MCP server

## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| katana repo | /Volumes/Data/code/self/katana | feat/wf-mcp | 主仓库 |
| agent-shell | /Volumes/Data/code/self/agent-shell | main | 工具脚本 |
| <placeholder> | <路径待填> | - | 占位 |

## 环境信息
- 本机 macOS
"""

CONTEXT_NO_TABLE = """\
# Context

**Updated:** 2026-06-21 10:00

## 工作上下文
- 无关键路径表
"""

CONTEXT_HEADER_ONLY = """\
## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
"""

# ---------------------------------------------------------------------------
# parse_context_paths
# ---------------------------------------------------------------------------

class TestParseContextPaths:
    def test_parses_two_real_rows_skips_placeholder(self):
        resources = parse_context_paths(CONTEXT_WITH_TABLE)
        assert len(resources) == 2

        r0 = resources[0]
        assert r0.name == "katana repo"
        assert r0.path == "/Volumes/Data/code/self/katana"
        assert r0.expected_branch == "feat/wf-mcp"

        r1 = resources[1]
        assert r1.name == "agent-shell"
        assert r1.path == "/Volumes/Data/code/self/agent-shell"
        assert r1.expected_branch == "main"

    def test_branch_placeholder_normalized_to_empty(self):
        # 分支列占位符（-）归一为 ""，避免误触发 DRIFT；真实分支名（含 /）保留
        md = (
            "## 关键路径\n"
            "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |\n"
            "|------|------------|------------|------|\n"
            "| svc | /Volumes/Data/svc | - | 非 git |\n"
            "| repo | /Volumes/Data/code/self/katana | feat/wf-mcp | git |\n"
        )
        rs = parse_context_paths(md)
        assert len(rs) == 2
        assert rs[0].expected_branch == ""
        assert rs[1].expected_branch == "feat/wf-mcp"

    def test_no_table_returns_empty(self):
        assert parse_context_paths(CONTEXT_NO_TABLE) == []

    def test_header_only_no_data_rows(self):
        # 只有表头和分隔行，没有数据行
        result = parse_context_paths(CONTEXT_HEADER_ONLY)
        assert result == []

    def test_skips_row_with_empty_path(self):
        md = """\
## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| 空路径 |  | - | 测试 |
| 正常 | /some/path | main | ok |
"""
        result = parse_context_paths(md)
        assert len(result) == 1
        assert result[0].path == "/some/path"

    def test_skips_placeholder_angle_bracket(self):
        md = """\
## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| foo | <待填写> | - | 占位 |
| bar | /real/path | dev | 真实 |
"""
        result = parse_context_paths(md)
        assert len(result) == 1
        assert result[0].name == "bar"

    def test_skips_path_containing_template_text(self):
        md = """\
## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| foo | 路径 / 地址 | - | 模板行 |
| bar | /real | main | ok |
"""
        result = parse_context_paths(md)
        assert len(result) == 1
        assert result[0].name == "bar"

    def test_no_expected_branch_when_dash(self):
        md = """\
## 关键路径
| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |
|------|------------|------------|------|
| file | /some/file.txt | - | 无分支期望 |
"""
        # "-" 在 parse 阶段即归一为 ""（无期望），避免 classify 误触发分支漂移。
        result = parse_context_paths(md)
        assert len(result) == 1
        assert result[0].expected_branch == ""


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------

class TestClassify:
    def _r(self, name="repo", path="/some/path", expected_branch="main"):
        return Resource(name=name, path=path, expected_branch=expected_branch)

    def _fact(self, exists=True, is_git=True, branch="main", dirty=False):
        return {"exists": exists, "is_git": is_git, "branch": branch, "dirty": dirty}

    def test_match_git_clean_branch_matches(self):
        v = classify(self._r(), self._fact())
        assert v.level == MATCH
        assert v.detail == "一致"

    def test_broken_when_not_exists(self):
        v = classify(self._r(path="/no/such/path"), self._fact(exists=False))
        assert v.level == BROKEN
        assert "路径不存在" in v.detail
        assert "/no/such/path" in v.detail

    def test_drift_branch_mismatch(self):
        v = classify(self._r(expected_branch="feat/x"), self._fact(branch="main"))
        assert v.level == DRIFT
        assert "分支漂移" in v.detail
        assert "feat/x" in v.detail
        assert "main" in v.detail

    def test_drift_dirty(self):
        v = classify(self._r(), self._fact(dirty=True))
        assert v.level == DRIFT
        assert "未提交" in v.detail

    def test_match_non_git_existing_path(self):
        # 存在但不是 git repo，也没有分支期望 → MATCH
        v = classify(self._r(expected_branch=""), self._fact(is_git=False, branch=""))
        assert v.level == MATCH

    def test_match_non_git_no_branch_expectation(self):
        # is_git=False，expected_branch="" → 跳过分支检查 → MATCH
        v = classify(
            self._r(expected_branch=""),
            self._fact(is_git=False, branch="", dirty=False),
        )
        assert v.level == MATCH

    def test_broken_takes_priority_over_drift(self):
        # 不存在时，即使 dirty=True 也是 BROKEN
        v = classify(self._r(), self._fact(exists=False, dirty=True))
        assert v.level == BROKEN

    def test_branch_mismatch_takes_priority_over_dirty(self):
        # 分支漂移 + dirty → DRIFT（分支漂移先触发）
        v = classify(
            self._r(expected_branch="feat/x"),
            self._fact(branch="main", dirty=True),
        )
        assert v.level == DRIFT
        assert "分支漂移" in v.detail

    def test_verdict_carries_resource_name_and_path(self):
        r = self._r(name="my-repo", path="/my/path")
        v = classify(r, self._fact())
        assert v.name == "my-repo"
        assert v.path == "/my/path"

    def test_no_branch_expectation_no_drift_even_if_different_branch(self):
        # expected_branch="" → 不检查分支，dirty=False → MATCH
        v = classify(
            self._r(expected_branch=""),
            self._fact(is_git=True, branch="some-other-branch", dirty=False),
        )
        assert v.level == MATCH


# ---------------------------------------------------------------------------
# verify_env
# ---------------------------------------------------------------------------

class TestVerifyEnv:
    def test_three_resources_in_order(self):
        resources = [
            Resource("r0", "/path/0", "main"),
            Resource("r1", "/path/1", "dev"),
            Resource("r2", "/path/2", ""),
        ]

        facts = {
            "/path/0": {"exists": True, "is_git": True, "branch": "main", "dirty": False},
            "/path/1": {"exists": True, "is_git": True, "branch": "feat", "dirty": False},  # 分支漂移
            "/path/2": {"exists": False, "is_git": False, "branch": "", "dirty": False},    # 不存在
        }

        def fake_probe(path):
            return facts[path]

        verdicts = verify_env(resources, probe_fn=fake_probe)
        assert len(verdicts) == 3
        assert verdicts[0].level == MATCH
        assert verdicts[1].level == DRIFT
        assert verdicts[2].level == BROKEN

    def test_empty_resources_returns_empty(self):
        assert verify_env([], probe_fn=lambda p: {}) == []


# ---------------------------------------------------------------------------
# overall_level
# ---------------------------------------------------------------------------

class TestOverallLevel:
    def test_any_broken_returns_broken(self):
        verdicts = [
            ResourceVerdict("a", "/a", MATCH, "一致"),
            ResourceVerdict("b", "/b", BROKEN, "不存在"),
            ResourceVerdict("c", "/c", DRIFT, "分支漂移"),
        ]
        assert overall_level(verdicts) == BROKEN

    def test_drift_without_broken(self):
        verdicts = [
            ResourceVerdict("a", "/a", MATCH, "一致"),
            ResourceVerdict("b", "/b", DRIFT, "分支漂移"),
        ]
        assert overall_level(verdicts) == DRIFT

    def test_all_match(self):
        verdicts = [
            ResourceVerdict("a", "/a", MATCH, "一致"),
            ResourceVerdict("b", "/b", MATCH, "一致"),
        ]
        assert overall_level(verdicts) == MATCH

    def test_empty_returns_match(self):
        assert overall_level([]) == MATCH

    def test_broken_beats_drift(self):
        # BROKEN 优先级最高
        verdicts = [
            ResourceVerdict("a", "/a", DRIFT, "漂移"),
            ResourceVerdict("b", "/b", BROKEN, "不存在"),
        ]
        assert overall_level(verdicts) == BROKEN


# ---------------------------------------------------------------------------
# fs_git_probe — smoke tests（真实 IO）
# ---------------------------------------------------------------------------

class TestFsGitProbeSmoke:
    WORKTREE_ROOT = "/Volumes/Data/code/worktrees/katana/wf-mcp"

    def test_worktree_root_is_git(self):
        fact = fs_git_probe(self.WORKTREE_ROOT)
        assert fact["exists"] is True
        assert fact["is_git"] is True
        # 分支应为 feat/wf-mcp（允许 startswith 匹配以防 detached HEAD 等情况）
        assert fact["branch"].startswith("feat/wf-mcp") or fact["branch"] == "feat/wf-mcp"

    def test_missing_path_returns_not_exists(self):
        fact = fs_git_probe("/this/path/definitely/does/not/exist/12345")
        assert fact["exists"] is False
        assert fact["is_git"] is False
        assert fact["branch"] == ""
        assert fact["dirty"] is False
