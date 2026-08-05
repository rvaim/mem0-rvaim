---
name: memory-reviewer
description: Reviews stored memory quality by detecting duplicates, contradictions, and stale entries with actionable recommendations. Use when search results seem conflicting, before running dream consolidation, or for periodic memory hygiene audits.
---

# mem0-rvaim Memory Reviewer

Audits memory quality for the current workspace. The quality analysis is
produced by the daemon's independent Memory LLM — you only present the
report.

## Steps

1. Call `consolidate_memories` with `dry_run=true` (analysis only).
2. Also call `get_memories` (`scope="workspace"`, `top_k=100`) for
   freshness context.
3. **Present the daemon's report verbatim**, structured as:

```
## Memory quality report

### Duplicates (<n>)
- [mem0:<id8>] <text>  →  merged into: <merged_text>

### Contradictions (<n>)
- [mem0:<id8>] vs [mem0:<id8>]: <note>

### Freshness
- Oldest: <date>   Newest: <date>   Count: <n>
```

4. If the user wants fixes, recommend the `mem0:dream` skill.

## Constraints

- **Read-only** — never delete or merge anything.
- Do not judge duplicates/contradictions yourself; present the daemon's
  analysis.
