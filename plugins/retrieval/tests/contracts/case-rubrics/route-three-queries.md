# Rubric: retrieval:route — route-three-queries

## Task
The skill was asked to route 3 queries to correct retrieval sources without actually fetching.

## Pass criteria (answer = YES only if ALL hold)
1. Query 1 (RX 7900 XTX 显卡 reddit 评价) → routed to `reddit` or `retrieval:reddit`
2. Query 2 (React useEffect 官方文档) → routed to `official-docs` or `retrieval:official-docs`
3. Query 3 (本地笔记里关于意式浓缩) → routed to `search-note` or `retrieval:search-note`

Each routing must be clearly named — not just implied. If any of the 3 routings is missing or wrong, answer NO.

## Output
Respond with exactly one JSON object:
```json
{"verdict": "yes", "reason": "<one sentence>"}
```
or
```json
{"verdict": "no", "reason": "<which routing was wrong or missing>"}
```
