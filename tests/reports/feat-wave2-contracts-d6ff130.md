# Contract Sweep Report

- branch: `feat/wave2-contracts` @ `d6ff130`
- date: 2026-06-07 11:42
- jobs: 1 / total: 758s
- **PASS 1 / FAIL 0 / SKIP 0 / NEEDS-REVIEW 0**

| case | result | 归因 | attempts | 耗时 | model | detail |
|---|---|---|---|---|---|---|
| fpa:fpa#fpa-full | PASS | — | 1 | 582s | lingzhi/claude-opus-4-8 |  |

## Skipped

- none

## NEEDS-REVIEW

- none

## Overall Verdict

All citations verify against the actual files: `raw/espresso-guide-b.md` does contain the 深焙/商用机型/96°C/94°C claims, `raw/espresso-guide-a.md` the 92°C/90°C 通用 claims, `coffee-basics.md` the 深焙 88–90°C reverse-direction reference, and `WIKI.md` the「禁止抹平」contract. No fabricated files or lines.

```json
{"items": [
  {"q": "PASS 但产物病态的 case", "answer": "no", "evidence": "唯一 PASS case fpa:fpa#fpa-full 的产物 FPA-espresso-temp-contradiction-coexist.md 是带行级引用的实质分析(N1-N5 需求拆解、Deconstruction/Reconstruction/Adversarial Review 全填实),adversarial-verdicts.json 含 3 条 upheld/revised/refuted 裁决且每条有 evidence;非空话/复读 prompt/占位符。核心引用经原文核验为真:raw/espresso-guide-b.md 确含『96°C…深焙豆…商用机型…低于94°C萃取不足』,raw/espresso-guide-a.md 含『92°C…低于90°C』,coffee-basics.md 含『深焙88–90°C』。"},
  {"q": "SKIP 集中到某 plugin 形成覆盖空洞", "answer": "no", "evidence": "报告 SKIP 0,Skipped 段为 none;不存在 SKIP,无从集中。但需注意本 sweep 仅 1 个 case(仅 fpa plugin),覆盖面本身极窄——这是范围问题,非 SKIP 空洞。"},
  {"q": "FAIL 归因与 detail 不自洽", "answer": "no", "evidence": "FAIL 0,表中唯一行 detail 为空、归因『—』;无 FAIL case 可供判定不自洽(vacuously 无矛盾)。"},
  {"q": "NEEDS-REVIEW judge 裁决引用不存在文件/页面的幻觉", "answer": "no", "evidence": "NEEDS-REVIEW 0 且 Overall Verdict 标注 not run,无 judge 裁决产物。附带:FPA 内部对抗裁决所引文件(WIKI.md L13、raw/espresso-guide-a/b、咖啡豆烘焙度.md、coffee-basics.md)均在 kb/ 实存;外部源(INEI/homegrounds/baristahustle 402)被诚实降级为 credibility medium 或标注检索受阻,无幻觉。"},
  {"q": "本次 sweep 整体可信", "answer": "yes", "evidence": "断言-产物一致:1 PASS、0 FAIL/SKIP/NEEDS-REVIEW,产物实质且引用经核验落地,机械验收 verify.sh 与内容质量相符。可信但范围有限——仅覆盖单个 case/单个 plugin(fpa),且 Overall Verdict(not run),不能据此推断其它 plugin 的回归健康度。"}
],
"summary": "唯一 PASS case 产物经原文核验为实质且引用真实、无幻觉,断言与产物自洽可信;但全 sweep 仅 1 case/1 plugin 且总裁决 not run,可信度仅限其覆盖范围,不代表整体回归覆盖充分。"}
```
