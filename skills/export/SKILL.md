---
name: export
description: Exports all memories to a portable Markdown file for backup or migration. Use when backing up memories, migrating to another machine, or archiving before cleanup.
---

# mem0-rvaim Export

Export memories from the local daemon to a portable Markdown file.

## Steps

1. Run:

```bash
python3 "<plugin-root>/scripts/admin.py" export > ~/.mem0/local/backups/mem0-export-$(date +%Y%m%d).md
```

- `export both` (default) — workspace + global
- `export workspace` — only the current workspace
- `export global` — only cross-project memories

2. Confirm the file size and entry count:

```bash
wc -l ~/.mem0/local/backups/mem0-export-*.md
```

3. Report: `Exported <n> lines to <path> (scope: both)`.

## Notes

- Exports contain no secrets — memory texts only, no tokens.
- Backups live in `~/.mem0/local/backups/`; they are never deleted by
  plugin upgrades or uninstalls.
