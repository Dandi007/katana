import os
from pathlib import Path
from katana_kb_mcp_shared import config

FIXTURE = str(Path(__file__).parent / "fixtures" / ".katana")


def test_get_reads_raw_value():
    assert config.get("work_folder_path", config_file=FIXTURE) == "智元工作/工作记录"


def test_get_default_when_missing():
    assert config.get("nonexistent", default="fallback", config_file=FIXTURE) == "fallback"


def test_env_var_overrides_file(monkeypatch):
    monkeypatch.setenv("KATANA_WIKI_ROOT", "override-root")
    assert config.get("wiki_root", env_var="KATANA_WIKI_ROOT", config_file=FIXTURE) == "override-root"


def test_resolve_returns_absolute(tmp_path):
    out = config.resolve("wiki_root", config_file=FIXTURE)
    assert os.path.isabs(out)
