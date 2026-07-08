#!/usr/bin/env bash
# 统一安装入口：把 memory-index 注入客户端装到本机各 runtime（user level，幂等）。
#
# 用法：install.sh [kimi-code|opencode|all]   # 默认 all
#
# - kimi-code：向 ~/.kimi-code/config.toml 追加 [[hooks]] UserPromptSubmit 条目
#   （command 指向本 repo 内脚本的绝对路径；已存在则跳过）
# - opencode：symlink plugin 到 ~/.config/opencode/plugins/
#   （OpenCode 的 plugin 扫描 glob 跟随 symlink）
# - Claude Code 不在此处：走 katana plugin 的 hooks/hooks.json（marketplace 安装）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
target="${1:-all}"

install_kimi() {
    local cfg="$HOME/.kimi-code/config.toml"
    if [ ! -f "$cfg" ]; then
        echo "skip kimi-code: $cfg not found"
        return
    fi
    local hook="$HERE/kimi-code/user-prompt-hook"
    if grep -qF "$hook" "$cfg"; then
        echo "kimi-code: already installed ($cfg)"
        return
    fi
    cat >>"$cfg" <<EOF

# katana-memory-mcp：每 session 首个 prompt 注入 <memory-index>（runtimes/install.sh 写入）
[[hooks]]
event = "UserPromptSubmit"
command = "$hook"
timeout = 10
EOF
    echo "kimi-code: hook appended to $cfg"
}

install_opencode() {
    if [ ! -d "$HOME/.config/opencode" ]; then
        echo "skip opencode: ~/.config/opencode not found"
        return
    fi
    local dir="$HOME/.config/opencode/plugins"
    mkdir -p "$dir"
    ln -sf "$HERE/opencode/katana-memory-index.ts" "$dir/katana-memory-index.ts"
    echo "opencode: plugin symlinked to $dir/katana-memory-index.ts"
}

case "$target" in
kimi-code) install_kimi ;;
opencode) install_opencode ;;
all)
    install_kimi
    install_opencode
    ;;
*)
    echo "usage: install.sh [kimi-code|opencode|all]" >&2
    exit 2
    ;;
esac
