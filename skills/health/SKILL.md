---
name: health
description: Diagnoses the local daemon, vector store, model providers, and memory read/write functionality. Use when memory operations fail, searches return empty, add_memory errors occur, MCP connection drops, or to verify the plugin is working correctly.
---

# mem0-rvaim Health Check

Run a diagnostic check on the local memory plugin.

## Steps

1. Call `memory_health` — returns daemon version, health flags, stats,
   and the session id.

2. If the daemon is unreachable, run:

```bash
python3 "<plugin-root>/scripts/admin.py" doctor
```

3. Verify providers from the effective config:

```bash
python3 "<plugin-root>/scripts/admin.py" config
```

Check that `llm.api_key`/`embedder.api_key` are set for remote APIs
(they show as `[REDACTED]` — presence is what matters), or that
`base_url` points at a reachable local server (Ollama etc.).

4. Run a write + read round-trip test:

```bash
python3 "<plugin-root>/scripts/admin.py" status
```

Then use `add_memory` with a test fact and `search_memories` to confirm
round-trip, then `delete_memory` to remove the test fact.

## Result reporting

Report each check as `✓ ok` or `✗ broken (hint)`:

- daemon running
- vector store (Qdrant Local)
- memory LLM configured/reachable
- embedder configured/reachable
- session registered
- write/read round-trip
