"""test_integration.py — work-folder MCP 全状态图集成回归 gate。

把「真机 e2e」（PR #53 验收时手跑的 live probe）固化成确定性自动测试：
驱动真实的 @mcp.tool() 薄壳（server.wf_*）+ 真实 fs_git_probe + 真实 artifacts I/O，
走完整生命周期 create → save → resume(MATCH/DRIFT/BROKEN) → list → search，
**无 svc、无 LLM、无网络**（仅 wf_search 的 vault-search 调用打桩）。

回归意义：任何改动若破坏「BROKEN→blocked / 三态分类 / artifact seed / changelog」，
本测试即红。补上之前只有手跑 e2e、没有自动门的缺口。
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

import katana_work_folder_mcp.server as server
from katana_kb_mcp_shared import vault_search as vs


def _run(coro):
    return asyncio.run(coro)


def _context_snapshot(resource_path: str, branch: str = "-") -> str:
    """生成一个带「关键路径」表的 context.md 快照。"""
    return (
        "# Context\n\n**Updated:** 2026-06-22 14:00\n\n"
        "## 工作上下文\n- 集成测试\n\n"
        "## 关键路径\n"
        "| 资源 | 路径 / 地址 | 分支 / 版本 | 备注 |\n"
        "|------|------------|------------|------|\n"
        f"| target | {resource_path} | {branch} | 探测目标 |\n\n"
        "## 环境信息\n- test\n"
    )


@pytest.fixture
def configured(tmp_path):
    """把 server 绑定到 tmp 下的 work-folder 根。"""
    kb = tmp_path
    wfroot = tmp_path / "工作记录"
    wfroot.mkdir()
    server.configure(str(wfroot), str(kb))
    return wfroot


# ---------------------------------------------------------------------------
# create → save → resume(MATCH)
# ---------------------------------------------------------------------------

def test_lifecycle_create_save_resume_match(configured, tmp_path):
    # create：seed progress + context
    created = _run(server.wf_create("集成测试 冒烟"))
    assert created["created"] is True
    folder = created["path"]
    assert (Path(folder) / "progress.md").exists()
    assert (Path(folder) / "context.md").exists()

    # save：生成 CLAUDE.md + AGENTS.md（相同），追加 changelog
    saved = _run(server.wf_save(folder, summary="集成存档"))
    assert saved["saved"] is True
    claude = Path(folder) / "CLAUDE.md"
    agents = Path(folder) / "AGENTS.md"
    assert claude.exists() and agents.exists()
    assert claude.read_text(encoding="utf-8") == agents.read_text(encoding="utf-8")
    assert "集成存档" in (Path(folder) / "progress.md").read_text(encoding="utf-8")

    # resume MATCH：context 指向一个存在的非 git 目录 → 真实 fs_git_probe 判 MATCH
    plain = tmp_path / "exists-plain"
    plain.mkdir()
    context_saved = _run(server.wf_save(
        folder,
        summary="更新关键路径",
        context_snapshot=_context_snapshot(str(plain)),
    ))
    assert context_saved["saved"] is True
    res = _run(server.wf_resume(folder))
    assert res["ok"] is True
    assert res["verification"]["overall"] == "MATCH"
    assert res["blocked"] is False
    # resume 追加了一行 resume changelog
    assert "resume" in (Path(folder) / "progress.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# resume(BROKEN) — 头号不变量：blocked=True 且走阻塞契约
# ---------------------------------------------------------------------------

def test_resume_broken_blocks(configured):
    folder = _run(server.wf_create("broken 场景"))["path"]
    context_saved = _run(server.wf_save(
        folder,
        summary="更新关键路径",
        context_snapshot=_context_snapshot("/nonexistent/wf-mcp-integration-xyz"),
    ))
    assert context_saved["saved"] is True
    res = _run(server.wf_resume(folder))
    assert res["ok"] is True
    assert res["verification"]["overall"] == "BROKEN"
    assert res["blocked"] is True
    # 返回的是阻塞契约，且报告点名缺失路径
    from katana_work_folder_mcp import lifecycle
    assert res["contract"] == lifecycle.RESUME_BLOCKED_CONTRACT
    assert "/nonexistent/wf-mcp-integration-xyz" in res["resume_report"]


# ---------------------------------------------------------------------------
# resume(DRIFT) — 真实 git repo + dirty
# ---------------------------------------------------------------------------

def test_resume_drift_real_dirty_git(configured, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.email=ci@ci", "-c", "user.name=ci",
             "-C", str(repo), *args],
            check=True, capture_output=True, text=True,
        )

    git("init", "-q")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-qm", "init")
    # 制造 dirty：新增未跟踪文件 → fs_git_probe.dirty=True → DRIFT
    (repo / "untracked.txt").write_text("y", encoding="utf-8")

    folder = _run(server.wf_create("drift 场景"))["path"]
    context_saved = _run(server.wf_save(
        folder,
        summary="更新关键路径",
        context_snapshot=_context_snapshot(str(repo)),
    ))
    assert context_saved["saved"] is True
    res = _run(server.wf_resume(folder))
    assert res["ok"] is True
    assert res["verification"]["overall"] == "DRIFT"
    assert res["blocked"] is False


# ---------------------------------------------------------------------------
# missing folder → ok=False, blocked=True（fail-safe）
# ---------------------------------------------------------------------------

def test_resume_missing_folder_is_blocked(configured):
    res = _run(server.wf_resume(str(configured / "不存在的目录")))
    assert res["ok"] is False
    assert res["blocked"] is True


# ---------------------------------------------------------------------------
# list + search
# ---------------------------------------------------------------------------

def test_list_returns_active_candidates(configured):
    _run(server.wf_create("候选 一"))
    _run(server.wf_create("候选 二"))
    lst = _run(server.wf_list(limit=5))
    assert len(lst["candidates"]) >= 2
    # 每条带 path/status/mtime
    for c in lst["candidates"]:
        assert "path" in c and "status" in c and "mtime" in c


def test_search_routes_through_vault_search(configured, monkeypatch):
    captured = {}

    def fake_search(query, **kwargs):
        captured["query"] = query
        captured["dir"] = kwargs.get("dir")
        return vs.SearchResponse(
            results=[vs.SearchResult(path="工作记录/x.md", score=0.9, title="X", snippet="...")],
            mode="hybrid",
        )

    monkeypatch.setattr(server.vault_search, "search", fake_search)
    res = _run(server.wf_search("vault 搜索 service", top_k=3))
    assert isinstance(res, list)
    assert res[0]["path"] == "工作记录/x.md"
    # scope 传到了 work-folder 子树
    assert captured["dir"] == server._scope
