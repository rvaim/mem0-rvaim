---
name: list-projects
description: Lists all known workspaces with stored memory counts and last activity. Use when checking which projects have memories, comparing memory distribution across repos, or finding a specific workspace scope.
---

# mem0-rvaim List Workspaces

Show all known workspace scopes for the current user (local).

## Steps

1. Call `list_workspaces`.

2. Print the table:

```
Workspace                     Sessions   Last seen
---------------------------   --------   ----------
<workspace_id>                <n>        <date>
```

3. Optionally append per-workspace memory counts by running:

```bash
python3 "<plugin-root>/scripts/admin.py" status
```

## Notes

- "workspace" is this plugin's name for what the official cloud plugin
  called "project" — the semantics are identical (one repo = one
  workspace).
- Only workspaces you have actually used appear here; nothing is shared
  across users or machines.
