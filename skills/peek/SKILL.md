---
name: peek
description: Searches memories and displays compact one-liner results, or looks up a specific memory by ID. Use for quick memory lookups, checking if a decision was recorded, resolving [mem0:id] citations, or browsing memories without full category detail.
---

# mem0-rvaim Peek

Quick local search with compact output.

## Steps

1. Call `search_memories` with:
   - `query="<the search term>"`
   - `scope="both"` (workspace + global)
   - `top_k=5`

   The daemon scopes the search to the current session's workspace and
   the shared global namespace — do not pass any `user_id`/`app_id`
   filters (they are not accepted).

2. Print compact one-liners:

```
[<scope>/<type>] <content, first 120 chars> [mem0:<id8>]
```

3. To resolve a `[mem0:<id>]` citation, call `get_memory` with
   `memory_id="<full id>"` and print the full content.
