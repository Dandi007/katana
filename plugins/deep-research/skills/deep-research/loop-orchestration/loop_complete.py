#!/usr/bin/env python3
"""通知 Loop MCP：goal 达成，停 loop。

用法：loop_complete.py <loop_id> <summary-file>
capability token 现签现用（lifetime 300s），secret 从 loop-mcp 自己的 env 文件读。
"""
import base64
import hashlib
import hmac
import json
import sys
import time
import urllib.request

ENV_PATH = "/home/uther/.config/loop-mcp/env"
URL = "http://127.0.0.1:7480/mcp"
SCOPE = "deep-research"


def read_secret() -> bytes:
    with open(ENV_PATH, encoding="utf-8") as fh:
        for line in fh:
            key, _, value = line.strip().partition("=")
            if key == "LOOP_MCP_CAPABILITY_SECRET":
                return value.encode()
    raise SystemExit("LOOP_MCP_CAPABILITY_SECRET not found in " + ENV_PATH)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def mint(secret: bytes, lifetime: int = 300) -> str:
    body = b64url(json.dumps(
        {"v": 1, "scope": SCOPE, "exp": int(time.time()) + lifetime},
        separators=(",", ":"),
    ).encode())
    sig = b64url(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"lm1.{body}.{sig}"


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: loop_complete.py <loop_id> <summary-file>")
    loop_id, summary_path = sys.argv[1], sys.argv[2]
    with open(summary_path, encoding="utf-8") as fh:
        summary = fh.read().strip() or "goal reached"

    req = urllib.request.Request(
        URL,
        data=json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "loop_complete",
                "arguments": {
                    "loop_id": loop_id,
                    "summary": summary[:2000],
                    "outcome": "completed",
                },
            },
        }).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + mint(read_secret()),
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(resp.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
