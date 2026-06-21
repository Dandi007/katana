"""模型配置：setter 仅供 env，model 一律显式（G5）。
build_env 回收 setter 产生的 ANTHROPIC_*/CLAUDE_CODE_* env，不碰 model 决策。
"""
import os, subprocess, yaml
from pathlib import Path

DEFAULT_PROFILE = os.path.expanduser("~/.config/agent-shell/profile.zsh")
# build_env 回收的 env 前缀
_KEYS = ("ANTHROPIC_", "CLAUDE_CODE_")


def load_models(repo_root) -> dict:
    """从 tests/models.yaml 加载模型配置。"""
    return yaml.safe_load((Path(repo_root) / "tests/models.yaml").read_text(encoding="utf-8"))


def build_env(setter: str, profile: str = DEFAULT_PROFILE) -> dict:
    """source profile + 跑 setter，回收它设的 ANTHROPIC_*/CLAUDE_CODE_* env（不含 model 决策）。"""
    import shlex
    if setter and not setter.replace("_", "").isalnum():
        raise ValueError(f"unsafe setter: {setter!r}")
    code = (f'source {shlex.quote(profile)} >/dev/null 2>&1; {setter} >/dev/null 2>&1; '
            f'env')
    out = subprocess.run(["zsh", "-c", code], capture_output=True, text=True).stdout
    env = {}
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if any(k.startswith(p) for p in _KEYS):
            env[k] = v
    return env


def role(repo_root, name: str):
    """返回 (setter, model) 元组。"""
    r = load_models(repo_root)["roles"][name]
    return r["setter"], r["model"]
