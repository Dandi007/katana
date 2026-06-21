"""复用 katana-config.sh 的配置 SSoT；不在 Python 重实现解析逻辑。"""
import os
import subprocess
from pathlib import Path


def _config_sh() -> str:
    """katana-config.sh 路径：env 覆盖 > repo 内 wiki 插件副本。"""
    env = os.environ.get("KATANA_CONFIG_SH")
    if env:
        return env
    # editable 安装下：config.py 在 mcp/shared/katana_kb_mcp_shared/ → repo 根上溯 4 层
    repo = Path(__file__).resolve().parents[3]
    path = repo / "plugins" / "wiki" / "hooks" / "katana-config.sh"
    if not path.exists():
        raise FileNotFoundError(
            f"katana-config.sh not found at inferred path {path}; "
            f"set KATANA_CONFIG_SH env to its absolute path"
        )
    return str(path)


def _run(subcmd: str, args: list[str], config_file: str | None) -> str:
    env = dict(os.environ)
    if config_file:
        env["KATANA_CONFIG_FILE"] = config_file
    proc = subprocess.run(
        ["bash", _config_sh(), subcmd, *args],
        capture_output=True, text=True, env=env, check=True,
    )
    return proc.stdout


def get(key: str, default: str = "", env_var: str = "", *, config_file: str | None = None) -> str:
    return _run("get", [key, default, env_var], config_file)


def resolve(key: str, default: str = "", env_var: str = "", *, config_file: str | None = None) -> str:
    return _run("resolve", [key, default, env_var], config_file)


def kb_root(*, config_file: str | None = None) -> str:
    return _run("kb-root", [], config_file)
