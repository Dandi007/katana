"""katana-wiki-cli — 给后台 harness 用的确定性出口（与 MCP tool 共用 lib 纯函数）。"""
from __future__ import annotations

import argparse
import json
import sys

from katana_kb_mcp_shared import config
from katana_wiki_mcp import enumerate as _enumerate
from katana_wiki_mcp import lint as _lint


def _resolve_wiki_root() -> str:
    return config.resolve("wiki_root", default=".", env_var="KATANA_WIKI_ROOT").strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="katana-wiki-cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list-docs")
    p_list.add_argument("--zone", default=None)
    p_lint = sub.add_parser("lint-mechanical")
    p_lint.add_argument("--path", default=None)
    args = parser.parse_args(argv)

    root = _resolve_wiki_root()
    if args.cmd == "list-docs":
        docs = _enumerate.enumerate_docs(root)
        if args.zone:
            z = args.zone.rstrip("/") + "/"
            docs = [d for d in docs if d["path"].startswith(z)]
        json.dump(docs, sys.stdout, ensure_ascii=False)
        return 0
    if args.cmd == "lint-mechanical":
        res = _lint.lint_mechanical(root, args.path)
        json.dump(res, sys.stdout, ensure_ascii=False)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
