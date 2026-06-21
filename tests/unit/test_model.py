import sys, pathlib, os
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness import model


def _profile(tmp):
    p = tmp / "profile.zsh"
    p.write_text(
        'set_claude_ccswitch_gpt(){ export ANTHROPIC_BASE_URL=http://127.0.0.1:15721; '
        'export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1; }\n')
    return str(p)


def test_build_env_collects_setter_env(tmp_path):
    env = model.build_env("set_claude_ccswitch_gpt", _profile(tmp_path))
    assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:15721"
    assert env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] == "1"


def test_roster_models_explicit(tmp_path):
    repo = pathlib.Path(__file__).resolve().parents[1].parent
    m = model.load_models(repo)
    gpt = next(x for x in m["jury-roster"] if x["name"] == "gpt")
    assert gpt["model"] == "gpt/gpt-5.5"   # 显式，非裸继承 setter 槽（5.4）
