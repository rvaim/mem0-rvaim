---
name: forget
description: Deletes memories by search query or memory ID with confirmation before removal. Use when removing outdated decisions, incorrect memories, sensitive data, or cleaning up after experiments. Also handles undo of recent additions.
---

# mem0-rvaim Forget

Delete specific memories from the local store.

## Execution

### By memory ID

1. Call `delete_memory` with `memory_id="<id>"`.
2. Confirm: `Deleted [mem0:<id8>]: "<first 80 chars>"`.

### By search query

1. Call `search_memories` (`scope="both"`, `top_k=10`) and show the results.
2. Ask the user which entries to delete; require explicit confirmation.
3. Delete each confirmed entry with `delete_memory`.
4. Report: `Deleted <n> memories.`

### Everything in a scope

- `delete_all_memories` with `scope="workspace"` deletes ALL memories of
  the current workspace — require the user to type the workspace id or
  "yes, delete all" before executing.
- `scope="global"` deletes all cross-project memories — even stronger
  confirmation required.
- Deleting another workspace's memories is impossible by design.

## Constraints

- Never delete without confirmation.
- Never attempt to pass `user_id`/`app_id` — the daemon rejects them.
