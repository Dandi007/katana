"""fixture cwd 子树的 before/after 快照 + delta。排除 harness 注入路径。"""
import hashlib
from pathlib import Path

HARNESS_EXCLUDE = ("claude-config", "home", "case.log", "case.trace.jsonl", ".jury")


def _excluded(rel: str, exclude) -> bool:
    first = rel.split("/", 1)[0]
    return first in exclude or rel in exclude


def snapshot(root, exclude=HARNESS_EXCLUDE) -> dict:
    root = Path(root)
    out = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = str(p.relative_to(root))
        if _excluded(rel, exclude):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def delta(before: dict, after: dict) -> dict:
    bk, ak = set(before), set(after)
    return {
        "created": ak - bk,
        "deleted": bk - ak,
        "modified": {k for k in bk & ak if before[k] != after[k]},
    }
