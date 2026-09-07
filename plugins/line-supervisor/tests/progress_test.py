"""监督脚本的代际排序与新单缓存缺失回归。"""
import ast
import contextlib
import glob
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/supervise/scripts"


class ProgressTests(unittest.TestCase):
    def test_generation_ten_wins_over_two(self):
        with tempfile.TemporaryDirectory() as root:
            dd = Path(root)
            (dd / "record.json").write_text(json.dumps({"dispatched_by": "wf-test"}))
            for gen in (2, 10):
                p = dd / f"g{gen}"
                p.mkdir()
                (p / "events.jsonl").write_text(json.dumps({
                    "stage": "implement", "event": f"generation-{gen}",
                    "at": "2026-09-07T00:00:00Z",
                }) + "\n")
            # 执行 readings.sh 的实际第三读数 Python 段；隔离外部活性探针。
            source = (SCRIPTS / "readings.sh").read_text()
            fragment = source.split('for d in "$FG_ROOT"/dd/*/; do python3 - "$d" "$LINE" <<\'EOF\'\n')[1].split("\nEOF")[0]
            prefix = "import subprocess\nsubprocess.run=lambda *a,**k: type('R',(),{'stdout':''})()\n"
            result = subprocess.run(["python3", "-c", prefix + fragment, root, "wf-test"],
                                    capture_output=True, text=True, check=True)
            self.assertIn("g10 implement generation-10", result.stdout)
            self.assertNotIn("generation-2", result.stdout)

    def test_missing_status_is_silent_and_other_orders_are_still_visited(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ("new", "other"):
                p = Path(root) / "dd" / name
                p.mkdir(parents=True)
                (p / "record.json").write_text('{"dispatched_by":"wf-test"}')
            (Path(root) / "dd/other/status.json").write_text('{"state":"running"}')
            # 仅加载实际 autowake 函数，避免模块顶层循环发起网络请求。
            tree = ast.parse((SCRIPTS / "monitor.py").read_text())
            fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "autowake")
            module = ast.Module(body=[fn], type_ignores=[])
            seen = []

            def read(path, *args, **kwargs):
                seen.append(str(path))
                return open(path, *args, **kwargs)

            scope = dict(time=time, glob=glob, os=os, json=json, FG_ROOT=root,
                         LINE="wf-test", open=read)
            exec(compile(module, str(SCRIPTS / "monitor.py"), "exec"), scope)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                scope["autowake"]()
            self.assertEqual(output.getvalue(), "")
            self.assertTrue(any(p.endswith("other/status.json") for p in seen))


if __name__ == "__main__":
    unittest.main()
