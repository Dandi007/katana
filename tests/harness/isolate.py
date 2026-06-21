"""隔离层：clonefile golden 副本 + 态卫生 base env + 隔离 HOME/CLAUDE_CONFIG_DIR。

搬自旧 tests/runner.py（ccs_online/build_base_env/sweep_setup）
与 tests/harness/case.py（_snapshot/HOME 注入）。保留机制，不照抄结构（G3）。
"""
import os, shutil, socket, subprocess, sys
from pathlib import Path

CCS_HOST, CCS_PORT = "127.0.0.1", 15721


# ---------------------------------------------------------------------------
# CCS 在线检测
# ---------------------------------------------------------------------------

def ccs_online() -> bool:
    """检查 CC Switch（127.0.0.1:15721）是否可达。"""
    try:
        with socket.create_connection((CCS_HOST, CCS_PORT), timeout=2):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 基础环境构造（态卫生）
# ---------------------------------------------------------------------------

def build_base_env(no_ccs_check: bool = False) -> dict:
    """构造 harness 子进程的基础环境覆盖层。

    与 trigger.py 里 {**os.environ, **env} 合并后生效，故用显式覆盖而非删键：
    - 删键无法覆盖 os.environ 里的值；
    - 键存在且值为空字符串才能在合并后盖掉宿主真实值（G3 态卫生）。

    态卫生项：
      KATANA_KB_ROOT=""       宿主 local.zsh 导出真实 KB 路径，子进程继承会绕过 fixture .katana；
                               空字符串使 katana kb-root 解析视为未设，回落 fixture 的 .katana。
      KATANA_CONFIG_FILE=""   防真实 ~/.katana 经 env 被采纳。
      CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1"
                               harness 流量过 ccs/lingzhi，后端拒 fine-grained-tool-streaming
                               beta header（400 invalid beta flag）。与 set_claude_ccswitch_*
                               setter 一致关掉。2026-06-21 live 契约验证。

    HOME 隔离在 case_env() 层（per-attempt mkdir），此处不注入。
    """
    env: dict = {}
    if not no_ccs_check:
        if not ccs_online():
            sys.exit("ABORT: ccs (127.0.0.1:15721) offline — 绝不 fallback 直连")
        env["ANTHROPIC_BASE_URL"] = f"http://{CCS_HOST}:{CCS_PORT}"
        # claude CLI requires ANTHROPIC_API_KEY for API-key mode（非 OAuth）。
        # ccs 不校验 token 合法性；任意非空字符串均可；可被 ANTHROPIC_AUTH_TOKEN 覆盖。
        env["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_AUTH_TOKEN", "ccs-local")
    # 态卫生：显式覆盖为空，使宿主真实值在 {**os.environ, **env} 合并后失效。
    env["KATANA_KB_ROOT"] = ""
    env["KATANA_CONFIG_FILE"] = ""
    env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
    return env


# ---------------------------------------------------------------------------
# 隔离 HOME / CLAUDE_CONFIG_DIR（per-case）
# ---------------------------------------------------------------------------

def case_env(base_env: dict, case_dir: Path) -> dict:
    """在 base_env 上叠加隔离的 HOME 与 CLAUDE_CONFIG_DIR。

    HOME 隔离防 ~/.katana 兜底命中真实文件（KATANA_KB_ROOT 已在 build_base_env
    置空，这是双重保险；也防其他 ~ 展开路径误命中宿主配置）。
    """
    case_dir = Path(case_dir)
    home = case_dir / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        **base_env,
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(case_dir / "claude-config"),
    }


# ---------------------------------------------------------------------------
# Clonefile 复制（APFS 写时复制）
# ---------------------------------------------------------------------------

def case_clone(golden: Path, case_dir: Path) -> None:
    """将 golden 快照 clonefile 到 case_dir（APFS 写时复制）。

    失败时回退 shutil.copytree；case_dir 残留先清（防嵌套复制）。
    """
    golden, case_dir = Path(golden), Path(case_dir)
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cp", "-c", "-R", str(golden), str(case_dir)],
                       capture_output=True)
    if r.returncode != 0:
        shutil.copytree(golden, case_dir)


# ---------------------------------------------------------------------------
# Golden 快照构造（每次 sweep 调用一次）
# ---------------------------------------------------------------------------

def golden_setup(repo: Path, tmp: Path, plugins: set, claude_bin: str) -> Path:
    """构造 golden 快照目录：fixture copytree + marketplace add + plugin install。

    每次 sweep 的 CLAUDE_CONFIG_DIR 是 fresh 副本，marketplace add 不存在跨 sweep
    幂等性问题。各 case 通过 case_clone() 获得独立副本，互不干扰。
    """
    repo, tmp = Path(repo), Path(tmp)
    golden = tmp / "golden"

    # 1. fixture copytree 进 golden（先用普通 copytree 建 golden，后续 case 用 clonefile）
    for fx in sorted((repo / "tests/fixtures").iterdir()):
        if fx.is_dir():
            shutil.copytree(fx, golden / fx.name)

    # 2. 安装插件到 golden 的 CLAUDE_CONFIG_DIR
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(golden / "claude-config")}

    def cc(*args):
        r = subprocess.run([claude_bin, *args], env=env, capture_output=True,
                           text=True, timeout=300)
        if r.returncode != 0:
            sys.exit(
                f"ABORT golden-setup: claude {' '.join(args)} failed:\n"
                f"{r.stdout}{r.stderr}"
            )

    cc("plugin", "marketplace", "add", str(repo))
    for p in sorted(plugins):
        cc("plugin", "install", f"{p}@katana")

    return golden
