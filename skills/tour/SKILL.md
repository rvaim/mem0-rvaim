---
name: tour
description: Browses stored memories grouped by scope and type with full content display. Use when reviewing all memories, exploring stored knowledge, onboarding to a project, or getting an overview of captured decisions, conventions, and learnings.
---

# mem0-rvaim Tour

Show the user what the local memory store contains for the current
workspace and the global scope.

## Steps

1. Call `get_memories` with `scope="both"` and `top_k=200`.
2. Group results by `scope` (workspace/global), then by
   `metadata.memory_type` or `metadata.type`.
3. Print grouped with full content:

```
## Workspace — <workspace_id>

### decisions
- [mem0:<id8>] <full content>

### session_summary
- [mem0:<id8>] <full content>

## Global

### user_preference
- [mem0:<id8>] <full content>
```

4. If a group is empty, omit it. If everything is empty, say
   "No memories stored yet — use /mem0:remember to store the first one."
