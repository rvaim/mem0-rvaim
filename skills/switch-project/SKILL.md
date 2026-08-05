---
name: switch-project
description: Overrides the auto-detected workspace scope for the current directory. Use when auto-detection resolves to the wrong workspace, or when merging a folder into an existing workspace.
---

# mem0-rvaim Switch Workspace

Override the automatic workspace detection for the current directory.

## Steps

1. Ask the user for the target workspace id (a short slug, e.g.
   `my-project`).

2. Run:

```bash
PYTHONPATH="<plugin-root>" python3 -c "
from scripts._project import save_workspace_mapping
import os
save_workspace_mapping(os.getcwd(), '<workspace-id>')
"
```

3. Restart the session (or run `/mem0:health`) so the daemon registers
   the new mapping.

4. Confirm: `Workspace for this directory is now: <workspace-id>`.

## Notes

- The mapping is stored in `~/.mem0/local/config/workspace_map.json`
  and also keyed by git remote, so it survives folder renames.
- There is NO cross-user or cross-machine switching — memories are
  always scoped to this user + this workspace.
- The old "global search across all users/projects" mode is removed by
  design.
