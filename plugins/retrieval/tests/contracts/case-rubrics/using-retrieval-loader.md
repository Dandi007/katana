# Rubric: retrieval:using-retrieval — using-retrieval-loader

## Task
The skill was asked to list the retrieval conventions injected by the `using-retrieval` loader:
- What to do before answering factual questions (route to a source)
- What annotation to attach to retrieval conclusions (credibility/可信度)

## Pass criteria (answer = YES only if ALL hold)
1. Response mentions routing / consulting a source before answering
2. Response mentions credibility annotation (`high`/`medium`/`low` or `可信度`)
3. Response correctly reflects the skill's own conventions (not generic advice)

## Output
Respond with exactly one JSON object:
```json
{"verdict": "yes", "reason": "<one sentence>"}
```
or
```json
{"verdict": "no", "reason": "<which criterion failed>"}
```
