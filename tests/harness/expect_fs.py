"""轴② delta 断言。created/modified/deleted（glob）/content（grep delta 文件）/
unchanged_outside（无越界写）/script（逃逸口）。"""
from dataclasses import dataclass
from pathlib import Path
import fnmatch, json, re, subprocess


@dataclass
class Result:
    type: str
    ok: bool
    detail: str = ""


def _match(glob, names):
    return [n for n in names if fnmatch.fnmatch(n, glob)]


def check_fs(asserts, delta, cwd, contract_dir):
    cwd, contract_dir = Path(cwd), Path(contract_dir)
    # 累积所有 created/modified/deleted 声明的 glob，供 unchanged_outside 消费
    # unchanged_outside 必须放断言列表最后，才能看到前面所有声明
    declared = set()
    out = []
    for a in asserts:
        ((typ, val),) = a.items()
        if typ in ("created", "modified", "deleted"):
            declared.add(val)
            hit = _match(val, delta[typ])
            out.append(Result(typ, bool(hit), "" if hit else f"no {typ} match: {val}"))
        elif typ == "content":
            p = cwd / val["path"]
            ok = p.exists() and re.search(val["matches"], p.read_text(encoding="utf-8"), re.M)
            out.append(Result(typ, bool(ok), "" if ok else f"{val['path']} !~ {val['matches']}"))
        elif typ == "unchanged_outside":
            changed = delta["created"] | delta["modified"] | delta["deleted"]
            stray = [c for c in changed if not any(fnmatch.fnmatch(c, g) for g in declared)]
            out.append(Result(typ, not stray, "" if not stray else f"stray writes: {stray[:5]}"))
        elif typ == "script":
            script = (contract_dir / val).resolve()
            if not script.is_relative_to(contract_dir.resolve()):
                out.append(Result(typ, False, f"script escapes: {val}"))
                continue
            env = {
                "CWD": str(cwd),
                "DELTA_JSON": json.dumps({k: sorted(v) for k, v in delta.items()}),
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            }
            r = subprocess.run(
                ["bash", str(script)], env=env,
                capture_output=True, text=True, timeout=120,
            )
            out.append(Result(
                typ, r.returncode == 0,
                (r.stdout + r.stderr)[-300:] if r.returncode else "",
            ))
        else:
            out.append(Result(typ, False, f"unknown fs assert: {typ}"))
    return out
