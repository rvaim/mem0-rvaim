---
name: pin
description: Pins or unpins a memory by updating its metadata, protecting it from pruning during dream consolidation. Use when a memory is critical and must never be removed, such as architecture decisions, security constraints, or immutable team conventions.
---

# mem0-rvaim Pin

Pin a memory to mark it as high-priority. Pinning is stored as real
metadata (`pinned: true`) — the memory text is never modified.

## Execution

### Pin

1. Locate the memory: `search_memories` with a query, or resolve a
   `[mem0:id]` citation.
2. Call `update_memory` with:
   - `memory_id="<id>"`
   - `metadata_patch={"pinned": true, "pinned_at": "<ISO date>"}`
3. Confirm: `Pinned [mem0:<id8>]: "<first 80 chars>"`.

### Unpin

1. Call `update_memory` with `metadata_patch={"pinned": false}`.
2. Confirm: `Unpinned [mem0:<id8>].`

## Notes

- `pinned` memories are excluded from `consolidate_memories` suggestions.
- Pinning never prepends `[PINNED]` to the memory content — metadata is
  authoritative.
