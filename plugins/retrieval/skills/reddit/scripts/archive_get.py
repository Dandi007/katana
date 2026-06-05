#!/usr/bin/env python3
"""arctic-shift 存档降级：reddit.com 对 datacenter IP 403 时取帖/评论。
Usage: archive_get.py post <id> | comments <link_id> [--limit N]
ENV: RETRIEVAL_ARCHIVE_API (default arctic-shift), HTTPS_PROXY 透传给 urllib。
"""
import json, sys, urllib.request, os
API = os.environ.get("RETRIEVAL_ARCHIVE_API", "https://arctic-shift.photon-reddit.com")

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "retrieval-archive/1.0"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode())

def main():
    if len(sys.argv) < 3:
        print("usage: archive_get.py post <id> | comments <link_id> [--limit N]", file=sys.stderr); sys.exit(2)
    kind = sys.argv[1]
    if kind == "post":
        d = fetch(f"{API}/api/posts/ids?ids={sys.argv[2]}")
    elif kind == "comments":
        lim = "100"
        if "--limit" in sys.argv: lim = sys.argv[sys.argv.index("--limit")+1]
        d = fetch(f"{API}/api/comments/search?link_id={sys.argv[2]}&limit={lim}")
    else:
        print("usage: archive_get.py post <id> | comments <link_id> [--limit N]", file=sys.stderr); sys.exit(2)
    print(json.dumps(d.get("data", d), ensure_ascii=False))

if __name__ == "__main__":
    main()
