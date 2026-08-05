---
name: onboard
description: Local setup wizard for mem0-rvaim: checks Python, the daemon, data directories, LLM and embedding providers, and runs one write+recall test. Use on first run in a new project, after configuration changes, or to re-run diagnostics.
---

# mem0-rvaim Onboarding Wizard

Local-only setup. No account, no API key from mem0 — you only configure
which LLM and embedding provider the daemon should use.

## Steps

### 1. Environment checks

Run:

```bash
python3 "<plugin-root>/scripts/admin.py" doctor
```

Verify:
- `data root` exists under `~/.mem0/local`
- `daemon healthy: True` (start it with `admin.py start` if not)
- `mem0 SDK: installed` (run `ensure_deps.sh` if not)
- `qdrant-client: installed`

### 2. Provider configuration

Show the effective config:

```bash
python3 "<plugin-root>/scripts/admin.py" config
```

If the LLM or embedder is not configured (no api_key), write
`~/.mem0/local/config/config.json`:

```json
{
  "llm": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key": "<your key>",
    "base_url": "https://api.openai.com/v1"
  },
  "embedder": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "api_key": "<your key>",
    "base_url": "https://api.openai.com/v1",
    "dimensions": 1536
  }
}
```

Any OpenAI-compatible endpoint works for `base_url` (Ollama, LM Studio,
vLLM, custom gateways). `dimensions` must match the embedding model.
Then reload: `python3 "<plugin-root>/scripts/admin.py" restart`.

### 3. Round-trip test

1. `add_memory` with `content="onboard test <timestamp>"`,
   `scope="workspace"`, `infer=false`.
2. `search_memories` with the same phrase — confirm the test memory is
   returned.
3. `delete_memory` to remove it.

### 4. Finish

Report:

```
✓ Python + venv
✓ Daemon (port <n>)
✓ Memory LLM (<provider>/<model>)
✓ Embedder (<provider>/<model>)
✓ Write/recall round-trip
```

## Notes

- Optional: `summary_llm` and `recall_llm` sections in config.json let
  you give summary and query-rewrite tasks their own model (defaults to
  the main `llm`).
- The daemon reloads config with `admin.py restart`.
