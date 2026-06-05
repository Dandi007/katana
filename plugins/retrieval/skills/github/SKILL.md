---
name: github
description: GitHub 检索源。Use when 需要查 repo/issue/PR/code search 或定位开源项目。gh CLI 优先，API backup。
---

# /retrieval:github

主路：`gh api` / `gh search repos` / `gh search code`。auth 由 gh 自管（`gh auth status`）。
与 /retrieval:code 协作：搜到目标 repo → 交给 code 源 clone 进 code root 求真。
