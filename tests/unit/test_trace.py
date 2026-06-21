import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness import trace

SAMPLE = '\n'.join([
    '{"type":"system","subtype":"init"}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"command":"jury:review"}}]}}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"panel.py --out .jury"}}]}}',
    'garbage line that is not json',
    '{"type":"result","subtype":"success","result":"done"}',
])

def test_load_trace_skips_bad_lines(tmp_path):
    p = tmp_path / "t.jsonl"; p.write_text(SAMPLE)
    events = trace.load_trace(p)
    assert any(e["type"] == "result" for e in events)

def test_tools_used(tmp_path):
    p = tmp_path / "t.jsonl"; p.write_text(SAMPLE)
    assert set(trace.tools_used(trace.load_trace(p))) == {"Skill", "Bash"}

def test_skills_loaded(tmp_path):
    p = tmp_path / "t.jsonl"; p.write_text(SAMPLE)
    assert trace.skills_loaded(trace.load_trace(p)) == ["jury:review"]
