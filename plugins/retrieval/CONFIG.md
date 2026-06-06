# retrieval plugin 配置键（写入项目 .katana）

| 键 | 含义 | 示例 | 密钥? |
|----|------|------|-------|
| retrieval_sources | 启用的源（逗号分隔） | web,reddit,twitter,official-docs,github,gitlab,linear,feishu,search-note,code,agent-session-search,xiaohongshu | 否 |
| web_proxy | web 抓取代理 | http://127.0.0.1:7897 | 否 |
| reddit_archive_api | reddit 降级存档 API | https://arctic-shift.photon-reddit.com | 否 |
| twitter_chrome_profile | twitter 登录态 profile 目录 | ~/.playwright-agent-profile | 否（登录态在目录内） |
| xiaohongshu_chrome_profile | 小红书登录态 profile 目录 | ~/.playwright-agent-profile | 否（登录态在目录内） |
| xiaohongshu_raw_dir | 小红书批量下载落盘根目录（相对路径基于项目根） | 转换文档/web | 否 |
| gitlab_host | gitlab 主机 | code.agibot.com | 否 |
| gitlab_token_env | 持有 gitlab token 的 env 变量名 | GITLAB_TOKEN_RW | 否（仅变量名） |
| linear_token_env | 持有 linear key 的 env 变量名 | LINEAR_API_KEY | 否 |
| search_note_embedding_url | 本地 embedding 端点 | http://<133>:18081/v1/embeddings | 否 |
| kb_dir | 本地知识库根（只读检索输入） | . | 否 |
| code_root_env | code root env 变量名 | AGENT_CODE_ROOT | 否 |
| code_root_fallback_env | code root 回退 env 变量名 | AGENT_CODE_ROOT_FALLBACK | 否 |
| code_clone_category_default | 自动 clone 默认落的类别子目录 | third_party | 否 |
| search_note_python | search-note 语义脚本的 python 解释器（需装 lancedb 才走 vector，否则脚本自降级 keyword） | ~/.cache/agent-knowledge/Zettelkasten/venv/bin/python | 否 |

**铁律**：raw token/key 绝不进 .katana。.katana 只放主机/路径/变量名；真密钥留 `~/.config/agent-shell/secrets.zsh`（host-local，不入 git）。
