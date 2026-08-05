---
name: remember
description: Stores a memory verbatim from user input with appropriate scope and metadata. Use when the user says remember this, save this, store this, note that, or explicitly asks to record a decision, preference, convention, or learning.
---

# mem0-rvaim Remember

Store a fact or learning into the local memory daemon.

## Execution

### Step 1: Extract the content

The user provides the content as an argument: `/mem0:remember <text>`

If no text was provided, ask: "What should I remember?"

### Step 2: Store

Call `add_memory` with:

- `content="<the user's text>"`
- `scope="auto"` — the independent Memory LLM classifies it as `global`
  (cross-project preferences) or `workspace` (project-specific). You do
  NOT need to decide the scope yourself.
- `infer=false` — the user stated the fact explicitly; no extraction needed.
- `metadata={"type": "<best classification>", "confidence": 1.0, "source": "remember_command"}`

Classification hints for `metadata.type` (the agent may pick; it only
affects the displayed category, not storage):

| Content signal | Type |
|---|---|
| "we decided...", "always use...", "never..." | `decision` |
| "X doesn't work because...", "don't try..." | `anti_pattern` |
| "I prefer...", "use X instead of Y" | `user_preference` |
| "the convention is...", "we always..." | `convention` |
| "learned that...", "figured out..." | `task_learning` |
| setup, env, tooling, config | `environmental` |
| anything else | `task_learning` |

### Step 3: Confirm

```
Remembered as <type> [<scope>]: "<content, first 80 chars>"
```

Append `...` only if content was truncated (longer than 80 chars).

Local writes are synchronous — no event polling needed.
