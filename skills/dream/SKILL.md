---
name: dream
description: Consolidates stored memories by merging duplicates, resolving contradictions, and pruning stale entries. Use when memory count is high, search results feel noisy or repetitive, or periodic cleanup is needed to maintain memory quality.
---

# mem0-rvaim Dream — Memory Consolidation

Consolidation pass over the current workspace's memories. All analysis
(duplicate detection, contradiction detection, merged text) is done by
the daemon's independent Memory LLM — you only execute what the user
approves.

## Steps

1. **Analyze (dry run)** — call `consolidate_memories` with
   `dry_run=true`. The daemon returns:

```json
{
  "duplicates": [{"ids": [...], "merged_text": "..."}],
  "contradictions": [{"ids": [...], "note": "..."}]
}
```

2. **Present** the suggestions to the user, excluding any memory whose
   metadata has `pinned: true`. For each suggestion show the texts and
   the proposed merged text.

3. **Apply** — after the user confirms, call `consolidate_memories` with
   `dry_run=false`. The daemon writes the merged memory and deletes the
   originals.

4. **Report**:

```
Dream consolidation: merged <n> groups, flagged <n> contradictions.
```

## Constraints

- Never apply without explicit user confirmation.
- Never analyze memory content yourself — the report comes from the
  daemon's Memory LLM.
- Pinned memories are protected server-side; do not attempt to bypass.
