"""trigger.run() 单元测试——全部走 fake-claude，不调真实 claude。"""
import sys, pathlib, os, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness import trigger

FAKE = str(pathlib.Path(__file__).resolve().parent / "fake-claude")


def test_single_turn_captures_trace_and_result(tmp_path):
    """单轮：trace.jsonl 存在，result_text 非空。"""
    r = trigger.run(
        prompt="hi",
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=["Bash"],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )
    assert pathlib.Path(r.trace_path).exists(), "trace_path 文件不存在"
    assert r.result_text, "result_text 应非空"


def test_single_turn_result_from_stream_json(tmp_path):
    """fake-claude 在 stream-json 模式吐 result 事件，result_text 应从其中提取。"""
    r = trigger.run(
        prompt="hello",
        cwd=tmp_path,
        log_dir=tmp_path,
        model="test-model",
        tools=[],
        timeout=60,
        env={"FAKE_CLAUDE_STDOUT": "expected-text"},
        claude_bin=FAKE,
    )
    assert "expected-text" in r.result_text


def test_trace_path_is_case_trace_jsonl(tmp_path):
    """trace 固定写 log_dir/case.trace.jsonl。"""
    r = trigger.run(
        prompt="p",
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )
    assert r.trace_path == str(tmp_path / "case.trace.jsonl")


def test_trace_contains_valid_json_lines(tmp_path):
    """trace.jsonl 里每行都是合法 JSON（fake-claude stream-json 吐 3 行）。"""
    r = trigger.run(
        prompt="p",
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )
    lines = [l for l in pathlib.Path(r.trace_path).read_text().splitlines() if l.strip()]
    parsed = [json.loads(l) for l in lines]  # 全部可解析，否则抛 JSONDecodeError
    assert any(ev.get("type") == "result" for ev in parsed)


def test_single_turn_exit_code(tmp_path):
    """fake-claude exit 0 → Result.exit_code == 0。"""
    r = trigger.run(
        prompt="p",
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )
    assert r.exit_code == 0


def test_nonzero_exit_code(tmp_path):
    """FAKE_CLAUDE_EXIT=1 → exit_code == 1。"""
    r = trigger.run(
        prompt="p",
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={"FAKE_CLAUDE_EXIT": "1"},
        claude_bin=FAKE,
    )
    assert r.exit_code == 1


def test_timeout_raises(tmp_path):
    """FAKE_CLAUDE_SLEEP=10，timeout=1 → ClaudeTimeout 抛出。"""
    import pytest
    with pytest.raises(trigger.ClaudeTimeout):
        trigger.run(
            prompt="p",
            cwd=tmp_path,
            log_dir=tmp_path,
            model="m",
            tools=[],
            timeout=1,
            env={"FAKE_CLAUDE_SLEEP": "10"},
            claude_bin=FAKE,
        )


def test_multi_turn_captures_trace_and_result(tmp_path):
    """多轮：末轮 trace 写 case.trace.jsonl，result_text 含两轮合并。"""
    r = trigger.run(
        turns=["turn-one", "turn-two"],
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )
    assert pathlib.Path(r.trace_path).exists()
    assert r.result_text  # 非空

    # 多轮中间 trace 文件也写了
    assert (tmp_path / "case0.trace.jsonl").exists()
    assert (tmp_path / "case1.trace.jsonl").exists()


def test_multi_turn_resume_propagated(tmp_path):
    """多轮时 fake-claude 会在 result 里附加 resumed:<session_id>，确认第二轮带 --resume。"""
    r = trigger.run(
        turns=["turn-one", "turn-two"],
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )
    # fake-claude 在 resumed 时在 result text 附加 "resumed:s1"
    assert "resumed:s1" in r.result_text


def test_both_prompt_and_turns_raises():
    """prompt 和 turns 同时传 → ValueError。"""
    import pytest
    with pytest.raises(ValueError):
        trigger.run(
            prompt="x",
            turns=["x"],
            cwd="/tmp",
            log_dir="/tmp",
            model="m",
            tools=[],
            timeout=60,
            env={},
        )


def test_neither_prompt_nor_turns_raises():
    """两者都不传 → ValueError。"""
    import pytest
    with pytest.raises(ValueError):
        trigger.run(
            cwd="/tmp",
            log_dir="/tmp",
            model="m",
            tools=[],
            timeout=60,
            env={},
        )


def test_multiturn_trace_accumulates_all_turns(tmp_path):
    """多轮：case.trace.jsonl 必须包含所有轮的事件，而不仅仅是末轮。
    fake-claude 每次调用都会吐含 Skill tool_use 的 assistant 事件，
    断言 turn1 的 Skill 事件没有丢失（skills_loaded 能看到 turn1 的 skill）。
    """
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from harness import trace as trace_mod

    r = trigger.run(
        turns=["turn-one", "turn-two"],
        cwd=tmp_path,
        log_dir=tmp_path,
        model="m",
        tools=[],
        timeout=60,
        env={},
        claude_bin=FAKE,
    )

    # canonical trace 必须存在
    trace_path = pathlib.Path(r.trace_path)
    assert trace_path.exists(), "case.trace.jsonl 不存在"

    events = trace_mod.load_trace(trace_path)

    # 两轮各吐 3 行(system+assistant+result)，拼接后至少 6 个事件
    assert len(events) >= 6, (
        f"多轮 trace 应包含所有轮事件（至少 6 个），实际只有 {len(events)} 个。"
        "turn1 的事件可能丢失。"
    )

    # fake-claude 每轮都会吐 Skill tool_use，skills_loaded 应能看到（来自 turn1）
    skills = trace_mod.skills_loaded(events)
    assert len(skills) >= 2, (
        f"多轮 trace 应含至少 2 条 Skill 事件（每轮各一条），实际: {skills}。"
        "turn1 的 skill_loaded 事件可能丢失。"
    )
