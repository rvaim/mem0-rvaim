# mem0-rvaim — Local Memory Plugin

mem0-rvaim is a local-first persistent memory plugin. A plugin-managed
Memory Daemon embeds the Mem0 Python library with Qdrant Local
(vector store) and SQLite (state). No cloud account, no API key,
no Docker, no PostgreSQL.

## How it works

- SessionStart registers the session with the daemon (session →
  workspace mapping).
- UserPromptSubmit asks the daemon to recall relevant memories and
  injects them as additional context. **Do not run your own parallel
  searches** — call `recall_context` if more context is needed.
- Stop / PreCompact ask the daemon to capture new transcript content
  (facts + session summary). **Never summarize or store memories
  yourself** — the daemon's independent Memory LLM does all extraction,
  classification, summarization and consolidation.
- The MCP server (`mem0`) proxies to the daemon. Tools: `add_memory`,
  `search_memories`, `get_memories`, `get_memory`, `update_memory`,
  `delete_memory`, `delete_all_memories`, `delete_entities`,
  `list_entities`, `recall_context`, `memory_health`, `list_workspaces`,
  `consolidate_memories`, `get_event_status`.

## Rules for the agent

1. **Never pass `user_id` or `app_id`** to any mem0 tool — the daemon
   rejects them; scope comes from the registered session.
2. `add_memory` with `scope="auto"` lets the Memory LLM classify
   global/workspace. `infer=false` for explicit statements.
3. Use `recall_context` instead of hand-built multi-query searches.
4. Use `consolidate_memories` (dry_run first) instead of analyzing
   memories yourself.
5. Local writes are synchronous; `get_event_status` always returns
   SUCCEEDED (compatibility shim).
6. `delete_all_memories` accepts `global` or `workspace` only — never
   "both", and never another workspace.
7. Memory data lives in `~/.mem0/local/data` — never write there
   directly (blocked by a hook).

## Skills

`mem0:remember`, `mem0:peek`, `mem0:forget`, `mem0:pin`, `mem0:health`,
`mem0:stats`, `mem0:tour`, `mem0:context-loader`, `mem0:dream`,
`mem0:memory-reviewer`, `mem0:export`, `mem0:import`,
`mem0:list-projects`, `mem0:switch-project`, `mem0:onboard`.

## Status line

On SessionStart the banner shows:
`Mem0 Active (local) | user=<user> | workspace=<workspace> | branch=<branch> | mode=local`
Display it as the opening line of your first response.
