# Task 11 Report: retrieval 13 契约迁三轴

**commit:** 36a3e55
**branch:** feat/e2e-harness-v2
**validation:** 14 retrieval contracts valid（含已迁 search-note-local）

---

## 每契约迁法（一句话）

| 契约 | 迁法 | stdout_grep 去向 |
|---|---|---|
| agent-session-search | 旧 2×stdout_grep("mihomo"/"session\|会话")→prompt 改要求落 `./agent-session-search-result.md`，filesystem.content 断言 `mihomo` | filesystem-content |
| code-local-repo | 旧 2×stdout_grep("lint-structure.sh"/"coverage-exemptions\|SKILLS\|COVERED")→落 `./code-local-repo-result.md`，filesystem.content 断言 lint-structure.sh；第二条 grep 只验文件名确认代码存在，可由第一条覆盖，合并到一条 content | filesystem-content |
| feishu-doc-search | 旧 2×stdout_grep("OKR"/"feishu\|lark")→落 `./feishu-doc-search-result.md`，content 断言 OKR；来源标注只是格式确认，不单独 content 断言（由 prompt 已要求"给出来源标注"保证） | filesystem-content（feishu 来源格式一条删，OKR 保留） |
| github-repo-lookup | 旧 3×stdout_grep("katana"/"main"/"github")→落 `./github-repo-lookup-result.md`，filesystem.content 断言 katana + main | filesystem-content |
| gitlab-project-lookup | 旧 2×stdout_grep("katana"/"gitlab\|glab")→落 `./gitlab-project-lookup-result.md`，filesystem.content 断言 katana；gitlab 工具名属措辞，删 | filesystem-content（工具名 grep 删） |
| linear-issue-query | 旧 2×stdout_grep("[A-Z]+-[0-9]+"/"linear\|Linear")→落 `./linear-issue-query-result.md`，content 断言 identifier 正则；Linear 名称删 | filesystem-content |
| official-docs-lookup | 旧 3×stdout_grep("stable\|稳定"/"docs.python.org"/"high\|medium\|low")→落 `./official-docs-lookup-result.md`，content 断言 stable + docs.python.org；可信度等级属措辞，删 | filesystem-content（credibility 等级 grep 删） |
| reddit-search | 旧 3×stdout_grep("reddit\|r/"/"https?://"/"high\|medium\|low")→落 `./reddit-search-result.md`，content 断言 reddit + URL；可信度等级删 | filesystem-content |
| twitter-fetch | 旧 2×stdout_grep("twttr\|jack"/"high\|medium\|low")→落 `./twitter-fetch-result.md`，content 断言 twttr\|jack；可信度等级删 | filesystem-content |
| web-fetch | 旧 3×stdout_grep("Example Domain"/"example.com"/"high\|medium\|low")→落 `./web-fetch-result.md`，content 断言 "Example Domain" + "example\\.com"；可信度等级删 | filesystem-content |
| route-three-queries | 旧 3×stdout_grep("reddit"/"official-docs"/"search-note")验路由名称→纯文字回答无文件产物，转 semantic（rubric 验三条路由全对） | semantic |
| using-retrieval-loader | 旧 3×stdout_grep("route"/"credibility\|可信度"/"high\|medium\|low")验规则文本→知识内化型回答无文件，转 semantic（rubric 验 route+credibility 规则均提到） | semantic |
| xiaohongshu-download | 旧 file_exists("{cwd}/小红书-*/index.md") + script→直接迁 filesystem（`created:"小红书-*/index.md"` + `script:xiaohongshu-download.verify.sh`）；process 加 skill_loaded | filesystem（已是产物型，无 stdout_grep） |

---

## 统计

- **filesystem-content 迁移：10 个**（agent-session-search / code-local-repo / feishu-doc-search / github-repo-lookup / gitlab-project-lookup / linear-issue-query / official-docs-lookup / reddit-search / twitter-fetch / web-fetch）
- **semantic 迁移：2 个**（route-three-queries / using-retrieval-loader）
- **已是产物型直接迁：1 个**（xiaohongshu-download，file_exists+script→filesystem）
- **已迁参照：1 个**（search-note-local，Task 10 已完成，本 task 未动）

## 删除的 stdout_grep 及原因

| 删除内容 | 原因 |
|---|---|
| `stdout_grep: "session\|会话"` (agent-session-search) | 措辞确认，命中笔记名已足够 |
| `stdout_grep: "coverage-exemptions\|SKILLS\|COVERED"` (code-local-repo) | 第一条文件名 grep 已锚定正确文件 |
| `stdout_grep: "feishu\|lark\|飞书"` (feishu-doc-search) | 来源标注格式确认，工具名措辞 |
| `stdout_grep: "github\\.com\|github"` (github-repo-lookup) | 平台名措辞，repo+branch 内容已足够 |
| `stdout_grep: "gitlab\|glab\|project"` (gitlab-project-lookup) | 工具名措辞 |
| `stdout_grep: "linear\|Linear"` (linear-issue-query) | 平台名措辞 |
| `stdout_grep: "high\|medium\|low"` (官方文档/reddit/twitter/web) | 可信度等级属措辞格式，非内容断言，4 处全删 |

## Concerns / 偏离

1. **route-three-queries / using-retrieval-loader 仅有 process（无 filesystem）**：满足不变量（process=1 skill_loaded），但无 filesystem。semantic 是软轴（NEEDS-REVIEW）。符合计划的"答问型落 semantic"策略，非偏离。
2. **xiaohongshu-download.verify.sh 环境变量**：原脚本用 `$KB_DIR`，但 expect_fs.check_fs 的 script 路径传入 `CWD`/`DELTA_JSON`，无 `KB_DIR`。脚本里 `find "$KB_DIR"` 在新 harness 下会因 `KB_DIR` 未定义而失败。这是存量脚本问题，超出本 task 范围（本 task 只迁 schema，不跑 live），记录为 concern，Task 14/15 修。
