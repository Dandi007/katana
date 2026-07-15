---
创建日期: <YYYY-MM-DD>
tags: [deep-research, clue-board]
类型: 索引
sources: ["deep-research:<topic>"]
摘要: "<研究线索状态快照，最多 40 字>"
research: <topic>
updated_round: <N>
status: exploring | converged | safety-cap | killed
---

# Clue Board: <研究标题>

研究入口：[[WIKI]]

> triage agent 每轮收尾时重写本快照（脚本无文件系统权限）。

## Frontier（下一轮待探，本轮 triage 选中）

| id | clue | suggested_sources | depth | rationale |
|----|------|-------------------|-------|-----------|
| c7 | <线索> | web | 2 | <为什么选它> |

## Visited（已探，不再重派）

| id | clue | round | findings | 备注 |
|----|------|-------|----------|------|
| c0 | <线索> | 1 | 8 | — |

## 每轮摘要

| round | +findings | +fresh clues | dropped | 停止判断 |
|-------|-----------|--------------|---------|----------|
| 1 | 12 | 5 | 2 | continue |
| 2 | 9 | 3 | 4 | continue |
| 3 | 4 | 0 | 3 | **converged**（原问题已可充分回答） |
