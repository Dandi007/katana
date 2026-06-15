---
name: using-katana
description: Use when starting any substantial piece of work - explains how katana plugins (work-folder, checkpoint, deep-research, memory) compose into one workflow for durable, resumable agent work
---

# Using Katana

katana 让 agent 工作可持久化：状态存文件、不存对话。已启用的 plugin（work-folder/checkpoint、memory、deep-research，及视配置启用的 wiki/writing/retrieval/feishu-docs）各有一句话 SessionStart 约定 + 按需 `/skill`。要点：跨 session / 多阶段工作先建 work folder；可复用的可验证事实存 memory card；research 产物落 work folder。各 plugin 的细则随对应 `/skill` 调用时载入。
