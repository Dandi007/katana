#!/usr/bin/env bash
# 真端到端：claude -p 驱动 搜索→详情→落盘，断言产物。
# 依赖活登录态 + 公网 + claude CLI；Playwright MCP 由本脚本经 --mcp-config 自带（npx @playwright/mcp），
# 不依赖调用方项目的 MCP 配置。
# 模型路由：透传 caller 的 ANTHROPIC_* env——可把 claude -p 指到便宜模型网关。
# repo 内不硬编码任何内部网关地址。
# 用法：
#   ./xiaohongshu.sh                                  # 用已安装的 /retrieval:xiaohongshu skill
#   SKILL_FILE=/path/to/SKILL.md ./xiaohongshu.sh     # merge 前直接用分支里的 skill 文件
#   KEEP_WORK_DIR=1 ./xiaohongshu.sh                  # 失败排查时保留产物目录
# 退出码：0=pass 1=fail 2=skip。不进 CI，本机 dogfood 手动跑。
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../../hooks/katana-config.sh"

PROF="$(katana_config_get xiaohongshu_chrome_profile "" "")"
case "$PROF" in "~"*) PROF="${HOME}${PROF#\~}";; esac
[ -n "$PROF" ] && [ -d "$PROF" ] || { echo "SKIP: no chrome profile"; exit 2; }
command -v claude >/dev/null || { echo "SKIP: no claude CLI"; exit 2; }
# profile 单实例：已被占用则 skip（不在测试脚本里替用户 kill）。
# 注意保留 = 形式：playwright 拉起的 Chrome cmdline 是 --user-data-dir=<path>（锁持有者）；
# 空格形式只会匹配 idle 的 MCP server 进程（不持锁），会造成误报 busy。
if pgrep -f "user-data-dir=$PROF" >/dev/null 2>&1; then
  echo "SKIP: profile busy (close the running agent browser first)"; exit 2
fi

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/xhs-e2e.XXXXXX")"
# 仅 PASS 时自动清理；FAIL 保留产物目录供排查（KEEP_WORK_DIR=1 则 PASS 也保留）
CLEANUP_ON_EXIT=0
trap 'if [ "$CLEANUP_ON_EXIT" = 1 ] && [ -z "${KEEP_WORK_DIR:-}" ]; then rm -rf "$WORK_DIR"; else echo "WORK_DIR kept: $WORK_DIR"; fi' EXIT
echo "WORK_DIR=$WORK_DIR"

if [ -n "${SKILL_FILE:-}" ]; then
  HOWTO="先完整阅读 ${SKILL_FILE} 并严格按其中的工作流执行"
  EXTRA_DIR="$(cd "$(dirname "$SKILL_FILE")" && pwd)"
else
  HOWTO="使用 /retrieval:xiaohongshu skill"
  EXTRA_DIR=""
fi

# 自带 Playwright MCP（隔离 profile）
MCP_CONFIG="$(printf '{"mcpServers":{"playwright":{"command":"npx","args":["-y","@playwright/mcp@latest","--user-data-dir","%s"]}}}' "$PROF")"

PROMPT="${HOWTO}：搜索小红书关键词「盒马 快手菜」，取赞数最高的 1 篇笔记抓取详情与评论，按 skill 的落盘格式通过 wiki MCP 下载到逻辑路径 转换文档/web/e2e。完成后输出 MCP 返回的逻辑文件清单；不得写 client workspace。"

# prompt 走 stdin：--add-dir 是变长参数，紧跟其后的位置参数会被吞成目录
( cd "$WORK_DIR" && printf '%s\n' "$PROMPT" | claude -p \
    --permission-mode acceptEdits \
    --mcp-config "$MCP_CONFIG" --strict-mcp-config \
    --allowedTools "mcp__playwright__browser_navigate,mcp__playwright__browser_evaluate,mcp__playwright__browser_wait_for,mcp__playwright__browser_snapshot,Write,Read,Edit" \
    ${EXTRA_DIR:+--add-dir "$EXTRA_DIR"} 2>&1 | tee "$WORK_DIR/claude.log" ) || { echo "FAIL: claude run errored"; exit 1; }

DIR="$(find "$WORK_DIR" -maxdepth 1 -type d -name "小红书-*" | head -1)"
[ -n "$DIR" ] || { echo "FAIL: no 小红书-* dir under $WORK_DIR"; exit 1; }
[ -f "$DIR/index.md" ] || { echo "FAIL: no index.md"; exit 1; }
grep -q '|' "$DIR/index.md" || { echo "FAIL: index.md has no table rows"; exit 1; }
NOTE="$(find "$DIR" -name "*.md" ! -name index.md | head -1)"
[ -n "$NOTE" ] || { echo "FAIL: no note md"; exit 1; }
grep -q "xsec_token" "$NOTE" || { echo "FAIL: url missing xsec_token"; exit 1; }
grep -q "^author:" "$NOTE" || { echo "FAIL: no author in frontmatter"; exit 1; }
grep -q "^likes:" "$NOTE" || { echo "FAIL: no likes in frontmatter"; exit 1; }
grep -q "^note_id:" "$NOTE" || { echo "FAIL: no note_id in frontmatter"; exit 1; }
grep -q "^fetched_at:" "$NOTE" || { echo "FAIL: no fetched_at in frontmatter"; exit 1; }
[ "$(wc -c < "$NOTE")" -gt 500 ] || { echo "FAIL: note too small (<500B)"; exit 1; }
CLEANUP_ON_EXIT=1
echo "PASS: e2e artifacts verified"
ls -la "$DIR"
