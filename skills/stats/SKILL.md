---
name: stats
description: Displays memory usage statistics for the current session and workspace including counts by scope, session activity, and daemon status. Use when checking how many memories exist, reviewing session activity, or auditing memory distribution.
---

# mem0-rvaim Stats

Show local memory statistics.

## Steps

1. Call `memory_health` — the result includes `stats` with
   `memory_counts` (global vs workspace), `sessions_known`, and
   `pending_retries`.

2. Optionally run:

```bash
python3 "<plugin-root>/scripts/admin.py" status
```

## Output format

```
Daemon: running (version 0.3.0)
Workspace: <workspace_id>
Memories:  <n> workspace, <n> global
Sessions known: <n>
Pending retries: <n>
```
