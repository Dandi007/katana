#!/usr/bin/env bash
# kimi-code UserPromptSubmit hook 回归：
# 无 session_id 不注入；服务不可达不落 marker（可重试）；同 session 只注入一次。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
HOOK="$HERE/../runtimes/kimi-code/user-prompt-hook"

export XDG_RUNTIME_DIR="$(mktemp -d)"
srv_pid=""
srv_dir=""
cleanup() {
    [ -n "$srv_pid" ] && kill "$srv_pid" 2>/dev/null || true
    rm -rf "$XDG_RUNTIME_DIR" "$srv_dir"
}
trap cleanup EXIT

# 1. 无 session_id → 空输出，退出码 0
out="$(printf '{}' | KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 bash "$HOOK")"
[ -z "$out" ] || { echo "FAIL: injected without session_id"; exit 1; }

# 2. 服务不可达 → 空输出，且不落 marker（下个 prompt 可重试）
out="$(printf '{"session_id":"s1"}' | KATANA_MEMORY_MCP_URL=http://127.0.0.1:1 bash "$HOOK")"
[ -z "$out" ] || { echo "FAIL: injected while service down"; exit 1; }
[ ! -e "$XDG_RUNTIME_DIR/katana-memory-kimi/s1.injected" ] || { echo "FAIL: marker written on failure"; exit 1; }

# 3. 服务可达 → 首次注入 <memory-index>，同 session 二次为空，新 session 再注入
srv_dir="$(mktemp -d)"
mkdir -p "$srv_dir/t/uther"
printf '<memory-index>\n- [m-000000] test-card — desc\n</memory-index>\n' > "$srv_dir/t/uther/index.md"
port_file="$XDG_RUNTIME_DIR/port"
python3 - "$srv_dir" "$port_file" >/dev/null 2>&1 <<'PY' &
import http.server, socketserver, sys, functools, pathlib
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=sys.argv[1])
with socketserver.TCPServer(("127.0.0.1", 0), handler) as httpd:
    pathlib.Path(sys.argv[2]).write_text(str(httpd.server_address[1]))
    httpd.serve_forever()
PY
srv_pid=$!
for _ in $(seq 1 50); do [ -s "$port_file" ] && break; sleep 0.1; done
[ -s "$port_file" ] || { echo "FAIL: test http server did not start"; exit 1; }
URL="http://127.0.0.1:$(cat "$port_file")"

out="$(printf '{"session_id":"s2"}' | KATANA_MEMORY_MCP_URL="$URL" bash "$HOOK")"
case "$out" in
    "<memory-index>"*) : ;;
    *) echo "FAIL: first prompt did not inject index"; exit 1 ;;
esac
out="$(printf '{"session_id":"s2"}' | KATANA_MEMORY_MCP_URL="$URL" bash "$HOOK")"
[ -z "$out" ] || { echo "FAIL: second prompt re-injected"; exit 1; }
out="$(printf '{"session_id":"s3"}' | KATANA_MEMORY_MCP_URL="$URL" bash "$HOOK")"
[ -n "$out" ] || { echo "FAIL: new session did not inject"; exit 1; }

echo "PASS: kimi user-prompt-hook dedup + degradation"
