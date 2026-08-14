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
INFO = "INFO"     # 不可 stat 的类型（endpoint/repo-ref/unknown），只作信息，不阻塞

# 资源类型（2.1 分型）
LOCAL_PATH = "local-path"  # 以 / 或 ./ 或 ~ 开头 → 走 fs/git 探测
ENDPOINT = "endpoint"      # http(s):// / ws(s):// 等 scheme → 默认不探活
REPO_REF = "repo-ref"      # repo:path 一类记法 → client-verified，不 stat
UNKNOWN = "unknown"        # 其余 → 不 stat，判 INFO，不得拉 BROKEN

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class Resource:
    """context.md 关键路径表中的一行资源记录。"""
    name: str
    path: str
    expected_branch: str = ""  # 来自"分支 / 版本"列；"" 表示无期望
    kind: str = LOCAL_PATH     # 资源类型（2.1 分型）


@dataclass
class ResourceVerdict:
    """对单个资源的验证结论。"""
    name: str
    path: str
    level: str    # MATCH | DRIFT | BROKEN | INFO
    detail: str   # 人类可读的说明
    kind: str = ""  # 对应资源的类型；"" 视为 local-path

# ---------------------------------------------------------------------------
# 1. parse_context_paths — 解析 context.md 中的关键路径表
# ---------------------------------------------------------------------------

# 匹配含"资源"和"路径"的表头行
_TABLE_HEADER_RE = re.compile(r"\|\s*资源\s*\|.*路径")

# endpoint：scheme:// 一类
_ENDPOINT_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")
# repo-ref：repo:path 一类（repo 名 + ':' + 非空白/非反斜杠路径）
_REPO_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*:[^\\\s]+$")


def _strip_markdown(path_cell: str) -> str:
    """剥掉第 2 列的 markdown 装饰后再判类型（2.2 装饰字符归一）。

    支持：成对反引号 `` `path` ``、加粗 `` **path** ``、行内链接 `` [text](path) `` 取 path。
    """
    s = path_cell.strip()
    # 行内链接 [text](path) → 取 path
    m = re.match(r"^\[[^\]]*\]\(([^)]*)\)$", s)
    if m:
        s = m.group(1).strip()
    # 成对反引号或加粗包裹（可嵌套，反复剥外层）
    changed = True
    while changed:
        changed = False
        if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
            s = s[1:-1].strip()
            changed = True
        elif len(s) >= 4 and s.startswith("**") and s.endswith("**"):
            s = s[2:-2].strip()
            changed = True
    return s


def _classify_kind(path: str) -> str:
    """按归一后的第 2 列值判定资源类型（2.1 资源分型）。"""
    if _ENDPOINT_RE.match(path):
        return ENDPOINT
    if path.startswith("/") or path.startswith("./") or path.startswith("~"):
        return LOCAL_PATH
    if _REPO_REF_RE.match(path):
        return REPO_REF
    return UNKNOWN


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

        # 2.2 装饰字符归一：先剥 markdown 装饰再判类型
        path = _strip_markdown(path)
        if not path:
            continue
        kind = _classify_kind(path)
        resources.append(
            Resource(name=name, path=path, expected_branch=expected_branch, kind=kind)
        )

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
            kind=resource.kind,
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
            kind=resource.kind,
        )

    # 规则 3：有未提交变更
    if fact["is_git"] and fact["dirty"]:
        return ResourceVerdict(
            name=resource.name,
            path=resource.path,
            level=DRIFT,
            detail="有未提交变更",
            kind=resource.kind,
        )

    # 规则 4：一切正常
    return ResourceVerdict(
        name=resource.name,
        path=resource.path,
        level=MATCH,
        detail="一致",
        kind=resource.kind,
    )

# ---------------------------------------------------------------------------
# 3. verify_env — 批量验证，使用注入的 probe_fn
# ---------------------------------------------------------------------------

def verify_env(resources: list[Resource], *, probe_fn) -> list[ResourceVerdict]:
    """按资源类型分发验证，保持顺序。

    probe_fn(path: str) -> dict  — 接受路径字符串，返回 fact dict。

    分型（2.1）：
        - local-path：展开 ~ 后走 probe_fn（fs/git 探测）
        - endpoint：不 stat，默认不探活（2.3），记 INFO
        - repo-ref：不 stat，标记 client-verified，记 MATCH
        - unknown：不 stat，记 INFO，不得拉 BROKEN
    """
    verdicts: list[ResourceVerdict] = []
    for r in resources:
        kind = r.kind or _classify_kind(r.path)
        if kind == LOCAL_PATH:
            probe_path = (
                os.path.expanduser(r.path) if r.path.startswith("~") else r.path
            )
            verdicts.append(classify(r, probe_fn(probe_path)))
        elif kind == ENDPOINT:
            verdicts.append(
                ResourceVerdict(
                    name=r.name,
                    path=r.path,
                    level=INFO,
                    detail="端点，默认不探活",
                    kind=ENDPOINT,
                )
            )
        elif kind == REPO_REF:
            verdicts.append(
                ResourceVerdict(
                    name=r.name,
                    path=r.path,
                    level=MATCH,
                    detail="repo 引用（client-verified），不 stat",
                    kind=REPO_REF,
                )
            )
        else:
            verdicts.append(
                ResourceVerdict(
                    name=r.name,
                    path=r.path,
                    level=INFO,
                    detail="未知类型，不 stat",
                    kind=UNKNOWN,
                )
            )
    return verdicts

# ---------------------------------------------------------------------------
# 4. overall_level — 汇总多个 verdict 的最高级别
# ---------------------------------------------------------------------------

def overall_level(verdicts: list[ResourceVerdict]) -> str:
    """汇总级别。

    BROKEN 只由 local-path 类资源的 verdict 决定（2.4）；
    其余类型（endpoint/repo-ref/unknown）最多贡献 DRIFT，永远拉不出 BROKEN。
    空列表返回 MATCH。
    """
    local_broken = any(
        v.kind in ("", LOCAL_PATH) and v.level == BROKEN for v in verdicts
    )
    if local_broken:
        return BROKEN
    if any(v.level in (DRIFT, BROKEN) for v in verdicts):
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
