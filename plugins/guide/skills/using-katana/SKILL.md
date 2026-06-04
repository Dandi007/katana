---
name: using-katana
description: Use when starting any substantial piece of work - explains how katana plugins (work-folder, checkpoint, deep-research, memory) compose into one workflow for durable, resumable agent work
---

# Using Katana

Katana is a toolkit for making agent work durable: state lives in files, not in the
conversation. Each plugin is independently installable; skip guidance for plugins
you don't have installed.

## The toolkit

| Plugin | What it gives you | When it activates |
|--------|-------------------|-------------------|
| work-folder | A per-task directory of control files (goal/spec/plan/progress/findings/golden-order/context) | Convention injected every session; create a work folder whenever work spans sessions or phases |
| work-folder › checkpoint skill | Save/resume the full working state of a session | Before /clear, before switching sessions, after milestones; resume in a fresh session |
| deep-research | Workflow-orchestrated multi-round research over your knowledge base + web, with cited report | When a question needs systematic exploration, not a one-shot answer |
| memory | Verified facts as memory cards, auto-injected as an index each session | When you learn a durable, verifiable fact worth keeping across all future sessions |

## How they compose

1. **Continuous work gets a work folder first.** Multi-session or multi-phase work
   starts by creating/binding a work folder. Record user decisions in
   `golden-order.md` as they happen.
2. **Checkpoint at the edges.** Before ending a session, run checkpoint (save).
   In the next session, run checkpoint (resume) — it loads state and verifies the
   environment hasn't drifted before continuing.
3. **Facts graduate to memory.** When work surfaces a reusable, verifiable fact
   (a path, an endpoint, a gotcha), save it as a memory card so every future
   session starts knowing it. Work-folder files are per-task; memory is forever.
4. **Research lands in the work folder.** deep-research writes its clue board,
   findings, and report under your knowledge base; link or copy conclusions into
   the active work folder so the task record stays self-contained.

## Rules of thumb

- File state beats conversation state. If it matters tomorrow, write it down.
- One work folder per topic; don't scatter control files.
- Memory cards record only verifiable facts, never task-local context.
