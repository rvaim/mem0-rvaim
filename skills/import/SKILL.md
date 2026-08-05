---
name: import
description: Imports memories from an exported Markdown file into the current workspace or the global scope. Use when migrating from another machine, restoring from backup, or setting up a new project with existing knowledge.
---

# mem0-rvaim Import

Import memories from a mem0-rvaim export file (or any `- [text]` style
markdown) into the local store.

## Steps

1. Confirm the target scope with the user:
   - `workspace` (default) — project-specific memories
   - `global` — cross-project preferences

2. Run:

```bash
python3 "<plugin-root>/scripts/admin.py" import <path-to-export.md> workspace
```

3. Report: `Imported <n> memories into <scope> scope.`

## Constraints

- Never import into another workspace — the daemon only accepts the
  current workspace or the global scope.
- Imported entries are written with `infer=false` (verbatim).
