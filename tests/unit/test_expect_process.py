"""tests/unit/test_expect_process.py

从旧 test_asserts.py 的 trace 用例改造：
  - 构造 trace.jsonl（assistant tool_use Skill + Bash）
  - 验证 skill_loaded / tool_used / tool_absent / tool_count / sequence 正负向
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from harness.expect_process import check_process


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_trace(tmp_path, lines: list[str]) -> pathlib.Path:
    p = tmp_path / "case.trace.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


_SKILL_LINE = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"jury:review"}}]}}'
_BASH_LINE  = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"echo hi"}}]}}'
_READ_LINE  = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/x"}}]}}'


# ---------------------------------------------------------------------------
# 正向测试：所有五种断言类型全过
# ---------------------------------------------------------------------------

def test_all_process_asserts_pass(tmp_path):
    """skill_loaded / tool_used / tool_absent / tool_count / sequence 正向全过。"""
    trace = _make_trace(tmp_path, [_SKILL_LINE, _BASH_LINE, _READ_LINE])

    asserts = [
        {"skill_loaded": "jury:review"},
        {"tool_used": "Bash"},
        {"tool_used": "Read"},
        {"tool_absent": "WebFetch"},
        {"tool_count": {"tool": "Bash", "eq": 1}},
        {"tool_count": {"tool": "Bash", "min": 1}},
        {"tool_count": {"tool": "Bash", "max": 2}},
        {"sequence": ["Bash", "Read"]},
    ]

    results = check_process(asserts, trace)
    assert all(r.ok for r in results), [vars(r) for r in results]


# ---------------------------------------------------------------------------
# 负向测试：skill 缺失、工具不匹配、序列缺失、trace 不存在
# ---------------------------------------------------------------------------

def test_skill_absent_returns_false(tmp_path):
    """trace 有事件但 skill 未被加载 → skill_loaded 为 False。"""
    # 只有 Bash，没有 Skill 事件
    trace = _make_trace(tmp_path, [_BASH_LINE])

    results = check_process([{"skill_loaded": "jury:review"}], trace)
    assert len(results) == 1
    r = results[0]
    assert not r.ok
    assert "jury:review" in r.detail


def test_tool_used_absent_and_count_failures(tmp_path):
    """tool_used 验不存在工具 / tool_absent 验已存在工具 / tool_count eq 不匹配均为 False。"""
    trace = _make_trace(tmp_path, [_SKILL_LINE, _BASH_LINE, _BASH_LINE])

    asserts = [
        {"tool_used": "WebFetch"},          # 从未使用 → False
        {"tool_absent": "Bash"},             # Bash 被用了 → False
        {"tool_count": {"tool": "Bash", "eq": 1}},  # 实际 count=2 → False
        {"tool_count": {"tool": "Bash", "max": 1}},  # max=1 但 count=2 → False
    ]

    results = check_process(asserts, trace)
    assert all(not r.ok for r in results), [vars(r) for r in results]


def test_sequence_broken_returns_false(tmp_path):
    """工具顺序不满足要求 → sequence 为 False。"""
    # 顺序是 Bash → Read；要求 Read → Bash（颠倒）
    trace = _make_trace(tmp_path, [_BASH_LINE, _READ_LINE])

    results = check_process([{"sequence": ["Read", "Bash"]}], trace)
    # Read 在位置 1，Bash 在位置 0 → 无法从 Read 之后找到 Bash → False
    # 注意：松子序列在 Read 之前先跳过 Bash，无法在 Read 后找到 Bash
    assert not results[0].ok
    assert "Bash" in results[0].detail


def test_no_trace_file_all_false(tmp_path):
    """trace_path 不存在时，所有断言均为 False + 'no trace captured'。"""
    missing = tmp_path / "nonexistent.trace.jsonl"

    asserts = [
        {"skill_loaded": "jury:review"},
        {"tool_used": "Bash"},
        {"sequence": ["Bash"]},
    ]

    results = check_process(asserts, missing)
    assert len(results) == 3
    assert all(not r.ok for r in results)
    assert all("no trace" in r.detail for r in results)


def test_no_trace_path_none_all_false(tmp_path):
    """trace_path 为 None 时，所有断言均为 False。"""
    results = check_process([{"skill_loaded": "x:y"}, {"tool_used": "Bash"}], None)
    assert all(not r.ok for r in results)
    assert all("no trace" in r.detail for r in results)
