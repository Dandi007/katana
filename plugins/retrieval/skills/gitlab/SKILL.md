---
name: gitlab
description: GitLab 检索源。查 project/branch/MR/issue/CI/code search。glab 优先，API backup；token 从 .katana 指定的 env 变量名读，host 从 gitlab_host 读。
---

# GitLab 检索源

优先通过 `glab` 访问公司 GitLab；当 `glab` 没有对应子命令、需要原始 API、或本机尚未完成 `glab` 认证时，再回退到 `glab api` / `curl`。

**硬约束**：凡查询 GitLab 平台数据（MR / issue / pipeline / 成员 / code search 等），**必须走 glab 或 GitLab API**，不得转用本地 clone 的 git 操作（fetch / log / remote）替代——本地视角不完整且答非所问（2026-08 盘点实锤的绕过案例）。

## 认证（env-var-name 间接引用）

token 和 host 均从 `.katana` 读取配置，**不在任何文件中硬编码 secret 值**。

```bash
# 从 .katana 拿配置键名
GITLAB_HOST="$(katana_config_get gitlab_host "code.agibot.com" "")"
TOK_ENV="$(katana_config_get gitlab_token_env "GITLAB_TOKEN_RO" "")"

# 按操作类型选 RO / RW（.katana 里 gitlab_token_env=GITLAB_TOKEN_RW 表示写操作默认）
# 读操作：用 TOK_ENV 指定的变量（通常 GITLAB_TOKEN_RO）
# 写操作：用 TOK_ENV 指定的变量（通常 GITLAB_TOKEN_RW）
TOK="$(eval echo "\${$TOK_ENV:-}")"
```

**Resolution 规则**：
- `.katana` 的 `gitlab_token_env` 指定变量名（如 `GITLAB_TOKEN_RW`）
- 读 op（view / list / GET）→ 优先使用 `GITLAB_TOKEN_RO`，没有则用 `TOK_ENV` 指向的变量
- 写 op（create / note / POST/PUT/DELETE）→ 使用 `TOK_ENV` 指向的变量（应为 RW）
- secret 本体存放于 `~/.config/agent-shell/secrets.zsh`，不入 Git，不出现在任何工具输出中

**安全注意**：不直接读/贴 `~/Library/Application Support/glab-cli/config.yml` 明文 PAT。排查时用 `glab auth status --hostname "$GITLAB_HOST"` 等脱敏命令。

**首次使用先 glab 登录**：
```bash
TOK_ENV="$(katana_config_get gitlab_token_env "GITLAB_TOKEN_RW" "")"
GITLAB_TOKEN="$(eval echo "\${$TOK_ENV:-}")"
GITLAB_HOST="$(katana_config_get gitlab_host "code.agibot.com" "")"
glab auth login --hostname "$GITLAB_HOST" --stdin < <(printf '%s' "$GITLAB_TOKEN")
glab auth status --hostname "$GITLAB_HOST"
```

## 检索操作

### 执行前检查

```bash
GITLAB_HOST="$(katana_config_get gitlab_host "code.agibot.com" "")"
glab auth status --hostname "$GITLAB_HOST"
```

### 获取 GitLab 版本（连通性检验）

```bash
TOK="$(eval echo "\${$(katana_config_get gitlab_token_env "GITLAB_TOKEN_RO" ""):-}")"
curl -s --header "PRIVATE-TOKEN: $TOK" "https://$GITLAB_HOST/api/v4/version"
```

### 获取项目信息

```bash
glab repo view group/project --output json
glab repo view group/subgroup/project --output json
```

### 列出用户可见的项目

```bash
glab repo list --member --per-page 20 --output json
```

### 获取 MR 列表

```bash
glab mr list -R group/project --all --output json
glab mr list -R group/project --target-branch main --output json
```

### 获取 Issue 列表

```bash
glab issue list -R group/project --all --output json
glab issue list -R group/project --assignee @me --output json
```

### 获取 Pipeline 列表

```bash
glab ci list -R group/project --per-page 20 --output json
glab ci list -R group/project --status failed --output json
```

### 获取分支列表（API）

```bash
GITLAB_HOST="$(katana_config_get gitlab_host "code.agibot.com" "")"
glab api "projects/<group>%2F<project>/repository/branches" --hostname "$GITLAB_HOST"
```

### 获取文件内容（API）

```bash
glab api "projects/<group>%2F<project>/repository/files/<file_path_url_encoded>/raw?ref=main" --hostname "$GITLAB_HOST"
```

### Code search（API）

```bash
# 全局搜索
TOK="$(eval echo "\${$(katana_config_get gitlab_token_env "GITLAB_TOKEN_RO" ""):-}")"
curl -s --header "PRIVATE-TOKEN: $TOK" \
  "https://$GITLAB_HOST/api/v4/search?scope=blobs&search=<keyword>&per_page=20"

# 项目内搜索
curl -s --header "PRIVATE-TOKEN: $TOK" \
  "https://$GITLAB_HOST/api/v4/projects/<group>%2F<project>/search?scope=blobs&search=<keyword>"
```

### curl backup 模式（glab 不可用时）

```bash
GITLAB_HOST="$(katana_config_get gitlab_host "code.agibot.com" "")"
TOK_ENV="$(katana_config_get gitlab_token_env "GITLAB_TOKEN_RO" "")"
TOK="$(eval echo "\${$TOK_ENV:-}")"

# 读操作
curl -s --header "PRIVATE-TOKEN: $TOK" \
     "https://$GITLAB_HOST/api/v4/<endpoint>"
```

### 处理项目路径

- `glab repo view` / `glab mr list -R` 直接用 `group/project`
- `glab api` / `curl` 模式下路径需 URL 编码（`/` → `%2F`）

| 原始路径 | 编码后 |
|---------|--------|
| `group/project` | `group%2Fproject` |
| `group/subgroup/project` | `group%2Fsubgroup%2Fproject` |

## 网络代理

公司内网 GitLab 走本机 mihomo：`127.0.0.1:7897`。**不要把 glab / git 的代理直接固定到上游出口（如 `172.22.62.133:17897`）**——那会绕过本机 mihomo 的路由、fallback 与环境一致性。

```bash
# 检查代理配置
glab config get proxy --host "$GITLAB_HOST"
git config --global --get "http.https://${GITLAB_HOST}/.proxy"
```

若 `no_proxy` 包含 `agibot.com`，代理被绕过 → 执行前 `unset no_proxy NO_PROXY`。

## 返回结果

- 优先 `--output json` / `--output ndjson` 获取结构化结果
- 如有分页，说明如何获取更多结果（`?page=N&per_page=100`）
- 如有错误，分析原因并给出建议

# References

- glab / GitLab API（host CLI）
