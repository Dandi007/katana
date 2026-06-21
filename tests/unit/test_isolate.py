"""tests/unit/test_isolate.py — Task 5 Step 1：隔离层单元测试。"""
import sys
import os
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness import isolate


def test_base_env_hygiene_strips_kb_root():
    """KATANA_KB_ROOT 即便在 os.environ 有真实值，build_base_env 也能用空串覆盖使其失效。"""
    os.environ["KATANA_KB_ROOT"] = "/tmp/REAL_KB"
    base = isolate.build_base_env(no_ccs_check=True)
    # 模拟 trigger.py 的合并方式：{**os.environ, **base}
    eff = {**os.environ, **base}
    assert eff.get("KATANA_KB_ROOT", "") == "", (
        f"KATANA_KB_ROOT leaked: {eff.get('KATANA_KB_ROOT')!r}"
    )


def test_case_env_isolates_home(tmp_path):
    """case_env 在 base_env 上叠加隔离的 HOME 和 CLAUDE_CONFIG_DIR，且均以 case_dir 为前缀。"""
    env = isolate.case_env({}, tmp_path)
    assert env["HOME"].startswith(str(tmp_path)), (
        f"HOME not under case_dir: {env['HOME']!r}"
    )
    assert env["CLAUDE_CONFIG_DIR"].startswith(str(tmp_path)), (
        f"CLAUDE_CONFIG_DIR not under case_dir: {env['CLAUDE_CONFIG_DIR']!r}"
    )
