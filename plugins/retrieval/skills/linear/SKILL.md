---
name: linear
description: Linear 检索源。GraphQL 查 issue/team/cycle/project；key 从 .katana 指定的 env 变量名读。
---

# Linear 检索源

通过 Linear GraphQL API 检索 issue、team、cycle、project 等信息。

## 认证（env-var-name 间接引用）

API key 从 `.katana` 读取变量名，**不在任何文件中硬编码 secret 值**。

```bash
# 从 .katana 拿变量名
TOK_ENV="$(katana_config_get linear_token_env "LINEAR_API_KEY" "")"

# 通过变量名间接取值
TOK="$(eval echo "\${$TOK_ENV:-}")"
```

secret 本体存放于 `~/.config/agent-shell/secrets.zsh`，不入 Git，不出现在任何工具输出中。

**连通性自检**：
```bash
TOK_ENV="$(katana_config_get linear_token_env "LINEAR_API_KEY" "")"
TOK="$(eval echo "\${$TOK_ENV:-}")"
curl -s -X POST https://api.linear.app/graphql \
  -H "Authorization: $TOK" \
  -H "Content-Type: application/json" \
  -d '{"query":"{ viewer { id name email } organization { name urlKey } }"}' | jq
```

## 检索操作

所有示例先按上述方式取 `TOK`，再构造 GraphQL 请求。下文以 `GQL` 函数表示：

```bash
GQL() {
  local query="$1"
  local vars="${2:-null}"
  curl -s -X POST https://api.linear.app/graphql \
    -H "Authorization: $TOK" \
    -H "Content-Type: application/json" \
    -d "$(jq -cn --arg q "$query" --argjson v "$vars" '{query:$q, variables:$v}')"
}
```

### 列 teams 与 workflow states

```bash
GQL '{ teams(first:50){ nodes{ id key name states(first:20){ nodes{ id name type } } } } }' \
  | jq '.data.teams.nodes[] | {key, name, states: [.states.nodes[] | {name,type}]}'
```

State type 分类：`backlog` / `unstarted`（Todo）/ `started`（In Progress）/ `completed`（Done）/ `canceled`。

### 查询"我被分配"的未完成 issue

```bash
VIEWER_ID=$(GQL '{ viewer { id } }' | jq -r .data.viewer.id)
GQL '
query MyOpen($id: String!) {
  issues(
    filter: {
      assignee: { id: { eq: $id } }
      state: { type: { nin: ["completed","canceled"] } }
    }
    first: 50
    orderBy: updatedAt
  ) {
    nodes { identifier title state{name} priority team{key} updatedAt url }
  }
}' "$(jq -cn --arg id "$VIEWER_ID" '{id:$id}')" | jq '.data.issues.nodes'
```

### 按 identifier 查 issue（如 `FI-42`）

```bash
GQL 'query($id:String!){ issue(id:$id){ id identifier title state{name type} assignee{name} team{id key} url } }' \
  '{"id":"FI-42"}' | jq
```

`query.issue(id:)` 同时接受 UUID 和 identifier（`TEAM-NUMBER`）。

### 搜索 issue（全文）

```bash
GQL '
query($term:String!){
  searchIssues(term:$term, first:20){
    nodes{ identifier title state{name} team{key} url }
  }
}' '{"term":"<keyword>"}' | jq '.data.searchIssues.nodes'
```

注意：使用 `searchIssues`，不要用 `issueSearch`（已废弃，runtime 返回 deprecated 错误）。

### 列 projects

```bash
GQL '{
  projects(first:50){
    nodes{ id name slugId state teams{ nodes{key} } }
  }
}' | jq '.data.projects.nodes'
```

### 列当前 cycles（迭代）

```bash
GQL '{
  cycles(first:20){
    nodes{ id number name startsAt endsAt team{key} }
  }
}' | jq '.data.cycles.nodes'
```

### 按 project slug 查项目详情

```bash
GQL '
query($q:String!){
  projects(filter:{slugId:{eq:$q}}, first:1){
    nodes{
      id name slugId state
      teams{ nodes{ key name } }
      members(first:50){ nodes{ displayName email } }
    }
  }
}' '{"q":"<project-slug>"}' | jq
```

### 列 team 成员

```bash
GQL '{
  teams(first:50){
    nodes{
      id key name
      members(first:50){ nodes{ displayName email } }
    }
  }
}' | jq
```

### 查 workspace 信息与 plan

```bash
GQL '{ organization{ name urlKey userCount subscription{type seats} } }' | jq
```

### Pagination 模板

```bash
cursor=null
while :; do
  resp=$(GQL '
    query($after:String){
      issues(first:50, after:$after, filter:{state:{type:{eq:"started"}}}){
        nodes{ identifier title }
        pageInfo{ hasNextPage endCursor }
      }
    }' "{\"after\":$cursor}")
  echo "$resp" | jq -r '.data.issues.nodes[] | "\(.identifier) \(.title)"'
  hasMore=$(echo "$resp" | jq -r '.data.issues.pageInfo.hasNextPage')
  [[ "$hasMore" == "true" ]] || break
  cursor=$(echo "$resp" | jq -c '.data.issues.pageInfo.endCursor')
done
```

## GraphQL 调用规范

1. `issue.id` = UUID；`issue.identifier` = `TEAM-NUMBER`——两者 `issue(id:)` 都接受
2. filter 内的 id 字段声明为 `ID!`，不是 `String!`，否则报类型错误
3. filter 语法：`filter: { field: { eq|neq|in|nin|contains|null: ... } }`
4. 全文搜索用 `searchIssues(term:)`，不要用 `issueSearch`
5. 变量用 `jq -cn` 组装，避免 shell 引号地狱

## 安全约束

- API key 存于 `~/.config/agent-shell/secrets.zsh`（`chmod 600`），不入 Git
- `.katana` 只存变量名（如 `LINEAR_API_KEY`），不存 secret 值
- 不在聊天、commit、日志中输出 key 内容

# References

- `.agents/skills/linear/SKILL.md` | source_type: internal | credibility: high — 完整 Linear ops skill（含写操作、Intake 分诊、profile 机制全集）
- <https://developers.linear.app/docs/graphql/working-with-the-graphql-api> | source_type: official | credibility: high
