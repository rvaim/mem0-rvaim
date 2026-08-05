---
name: context-loader
description: Injects relevant memories into context before starting work on a task. Use when beginning a new task, switching context, or when project history, past decisions, or coding conventions need to be loaded.
---

# mem0-rvaim Context Loader

Loads relevant local memories to prime context before working on a task.

## When to use

- Session start (invoke manually or auto-triggered by description matching)
- User starts work on a specific feature or file set
- Complex multi-step task begins
- User says "what do we know about X" or "context for X"

## Steps

1. **Call `recall_context` once** with:
   - `query="<the task description, file paths, module names, error patterns>"`
   - `mode="rewrite"` when the task is broad (the daemon's Recall LLM
     generates up to 3 targeted queries itself)

   The daemon searches the current workspace namespace AND the global
   namespace, merges, dedups, and enforces the token budget. You do NOT
   generate search queries.

2. **Present** the returned context compactly:

```
context-loader: loaded <n> memories for "<task summary>"
  - [<scope>/<type>] <content> [mem0:<short_id>]
```

3. If **zero results**: output nothing. Don't announce empty context.

## Constraints

- **Read-only** — never modify or delete memories
- **Max 10 memories** surfaced (most relevant only)
- **Silent on empty** — only surfaces findings if relevant context exists
- Never run parallel searches yourself — one `recall_context` call is
  the whole pipeline.
