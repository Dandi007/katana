"""verify.py — work-folder MCP server 的 L0 环境验证引擎。

纯函数层，无 LLM、无 server import、无 config import。
唯一 IO 函数是 fs_git_probe，其余全部通过注入的 probe_fn 驱动（可测试）。
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量：验证级别
# ---------------------------------------------------------------------------

MATCH = "MATCH"   # 环境与存档一致，可直接继续
DRIFT = "DRIFT"   # 环境有变化但不阻塞，报告差异后继续
BROKEN = "BROKEN" # 关键依赖不可用/缺失，必须停止，等待用户决策

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class Resource:
    """context.md 关键路径表中的一行资源记录。"""
    name: str
    path: str
    expected_branch: str = ""  # 来自"分支 / 版本"列；"" 表示无期望


@dataclass
class ResourceVerdict:
    """对单个资源的验证结论。"""
    name: str
    path: str
    level: str    # MATCH | DRIFT | BROKEN
    detail: str   # 人类可读的说明

# ---------------------------------------------------------------------------
# 1. parse_context_paths — 解析 context.md 中的关键路径表
# ---------------------------------------------------------------------------

# 匹配含"资源"和"路径"的表头行
_TABLE_HEADER_RE = re.compile(r"\|\s*资源\s*\|.*路径")


def parse_context_paths(context_md: str) -> list[Resource]:
    """从 context.md 文本中解析关键路径表，返回 Resource 列表。

    - 定位含"资源"和"路径"的表头行
    - 跳过表头行和 |---| 分隔行
    - 跳过路径为空、以 '<' 开头、或包含模板文字（路径 / 地址）的行
    - 返回 [] 如果没有匹配表格
    """
    lines = context_md.splitlines()

    # 找到表头行的索引
    header_idx = None
    for i, line in enumerate(lines):
        if _TABLE_HEADER_RE.search(line):
            header_idx = i
            break

    if header_idx is None:
        return []

    resources: list[Resource] = []

    # 从表头行后一行开始处理数据行
    for line in lines[header_idx + 1 :]:
        stripped = line.strip()
        # 不再是表格行时停止
        if not stripped.startswith("|"):
            break
        # 跳过 |---|---| 分隔行
        if re.match(r"^\|[-| :]+\|$", stripped):
            continue

        # 拆分列（去掉首尾的 "|" 后再 split）
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue

        name = cells[0].strip()
        path = cells[1].strip()
        expected_branch = cells[2].strip() if len(cells) > 2 else ""
        # 分支占位符归一：按 strip 后的精确相等判定（保留含 '/' 的真实分支名如 feat/wf-mcp）
        if expected_branch in {"-", "—", "/", "N/A", "n/a", "无"}:
            expected_branch = ""

        # 跳过路径为空的行
        if not path:
            continue
        # 跳过占位符（以 '<' 开头）
        if path.startswith("<"):
            continue
        # 跳过包含模板文字的行（原始表头文字）
        if "路径" in path or "地址" in path:
            continue

        resources.append(Resource(name=name, path=path, expected_branch=expected_branch))

    return resources

# ---------------------------------------------------------------------------
# 2. classify — 对单个资源打 MATCH/DRIFT/BROKEN 标签
# ---------------------------------------------------------------------------

def classify(resource: Resource, fact: dict) -> ResourceVerdict:
    """根据探针事实对资源进行分类。

    fact 结构：
        {"exists": bool, "is_git": bool, "branch": str, "dirty": bool}

    优先级（从高到低）：
        1. 路径不存在 → BROKEN
        2. git repo 且有分支期望且分支不符 → DRIFT
        3. git repo 且有未提交变更 → DRIFT
        4. 其余 → MATCH
    """
    # 规则 1：路径不存在 → BROKEN
    if not fact["exists"]:
        return ResourceVerdict(
            name=resource.name,
            path=resource.path,
            level=BROKEN,
            detail=f"路径不存在: {resource.path}",
        )

    # 规则 2：分支漂移（is_git + 有期望分支 + 分支不匹配）
    if (
        fact["is_git"]
        and resource.expected_branch
        and fact["branch"]
        and resource.expected_branch != fact["branch"]
    ):
        return ResourceVerdict(
            name=resource.name,
            path=resource.path,
            level=DRIFT,
            detail=f"分支漂移: 期望 {resource.expected_branch} 实为 {fact['branch']}",
        )

    # 规则 3：有未提交变更
    if fact["is_git"] and fact["dirty"]:
        return ResourceVerdict(
            name=resource.name,
            path=resource.path,
            level=DRIFT,
            detail="有未提交变更",
        )

    # 规则 4：一切正常
    return ResourceVerdict(
        name=resource.name,
        path=resource.path,
        level=MATCH,
        detail="一致",
    )

# ---------------------------------------------------------------------------
# 3. verify_env — 批量验证，使用注入的 probe_fn
# ---------------------------------------------------------------------------

def verify_env(resources: list[Resource], *, probe_fn) -> list[ResourceVerdict]:
    """对所有资源调用 probe_fn 并分类，保持顺序。

    probe_fn(path: str) -> dict  — 接受路径字符串，返回 fact dict。
    """
    return [classify(r, probe_fn(r.path)) for r in resources]

# ---------------------------------------------------------------------------
# 4. overall_level — 汇总多个 verdict 的最高级别
# ---------------------------------------------------------------------------

def overall_level(verdicts: list[ResourceVerdict]) -> str:
    """汇总级别：BROKEN > DRIFT > MATCH。空列表返回 MATCH。"""
    if any(v.level == BROKEN for v in verdicts):
        return BROKEN
    if any(v.level == DRIFT for v in verdicts):
        return DRIFT
    return MATCH

# ---------------------------------------------------------------------------
# 5. fs_git_probe — 唯一 IO 函数（生产环境探针）
# ---------------------------------------------------------------------------

def fs_git_probe(path: str) -> dict:
    """探测路径的文件系统与 git 状态。

    返回格式：
        {"exists": bool, "is_git": bool, "branch": str, "dirty": bool}

    - 所有 subprocess 错误静默处理，不抛出异常。
    - 不做任何修改性操作，纯只读探测。
    """
    exists = os.path.exists(path)
    if not exists:
        return {"exists": False, "is_git": False, "branch": "", "dirty": False}

    # git -C 需要目录；若 path 是文件，使用其父目录
    git_dir = path if os.path.isdir(path) else os.path.dirname(path)

    # 判断是否为 git 仓库并获取当前分支
    try:
        result = subprocess.run(
            ["git", "-C", git_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        is_git = result.returncode == 0
        branch = result.stdout.strip() if is_git else ""
    except Exception:
        is_git = False
        branch = ""

    # 检查是否有未提交变更
    dirty = False
    if is_git:
        try:
            status_result = subprocess.run(
                ["git", "-C", git_dir, "status", "--porcelain"],
                capture_output=True,
                text=True,
            )
            dirty = bool(status_result.stdout.strip())
        except Exception:
            dirty = False

    return {"exists": True, "is_git": is_git, "branch": branch, "dirty": dirty}
