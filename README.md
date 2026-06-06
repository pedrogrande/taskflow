# TaskFlow

A VS Code agent plugin that implements a **database-driven agentic pipeline** for software development. Every agent action is authorised and scoped by a task record. The database is the single source of truth for pipeline state, permissions, and context.

---

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`
- VS Code with GitHub Copilot (agent mode)

---

## Installation

Add to your VS Code `settings.json`:

```json
"chat.pluginLocations": [
  "/path/to/taskflow"
]
```

Or install from GitHub by adding the repo URL to `chat.pluginLocations`.

On first use, `uv` will automatically install the `mcp` dependency. No manual `pip install` required.

---

## Quick start

1. Open a project workspace in VS Code.
2. Type `/start-project` in the Copilot chat and follow the prompts to provide your project brief.
3. Use `/my-tasks` to see what each agent needs to do next.
4. Invoke the appropriate agent (e.g. **TaskFlow Product Manager**) to work the next task.
5. Use `/pipeline-status` at any time to see the full pipeline state.

---

## Slash commands

| Command | Description |
|---|---|
| `/start-project` | Start a new project — provide a brief file or inline text |
| `/my-tasks` | Show pending tasks for a chosen agent role |
| `/pipeline-status` | Show the full pipeline state for a project |

---

## Agents

| Agent | Role in pipeline |
|---|---|
| **TaskFlow Product Manager** | Defines features, decisions, and decision artefacts (steps 3, 10, 12) |
| **TaskFlow PM Reviewer** | Reviews and approves PM outputs (steps 2, 4, 11, 13) |
| **TaskFlow Tester** | Writes test specs and runs tests (steps 5, 8) |
| **TaskFlow Test Reviewer** | Reviews test specs (step 6) |
| **TaskFlow Builder** | Implements features (step 7) |
| **TaskFlow Documenter** | Writes retrospective and recommendations (step 9) |

---

## Pipeline overview

```
/start-project
      │
      ▼
  Step 3: PM defines features + DoD
      │
      ▼
  Step 4: PM Reviewer approves → spawns step 5 per feature
      │
      ▼ (per feature)
  Step 5: Tester writes test specs
  Step 6: Test Reviewer approves
  Step 7: Builder implements
  Step 8: Tester runs tests ──(fail × 3 → blocked)
      │ pass
      ▼
  Step 9: Documenter writes retro (auto-advances)
  Step 10: PM writes decisions
  Step 11: PM Reviewer approves
  Step 12: PM writes decision artefacts
  Step 13: PM Reviewer final verification
      │
      └──► Step 3 (next cycle)
```

New-feature decisions go to the **feature backlog**. The PM promotes them in step 3 of the next cycle.

---

## Schema summary

| Table | Purpose |
|---|---|
| `pipeline_steps` | 13-step workflow definition (seed data) |
| `projects` | Project records with brief text + optional file path |
| `features` | Feature records per project |
| `definitions_of_done` | Verifiable DoD criteria per feature |
| `test_specs` | Test specifications per feature |
| `build_reports` | Build output per feature per cycle |
| `test_results` | Pass/fail per test spec per build |
| `retro_reports` | Retrospective summaries per feature |
| `recommendations` | Recommendations from retros |
| `decisions` | Decisions made on recommendations |
| `decision_artefacts` | Patterns, gotchas, notes from step 12 |
| `feature_backlog` | New features queued for future cycles |
| `tasks` | Pipeline task queue (the engine) |

---

## Running tests

```bash
cd taskflow
uv run --with mcp pytest tests/ -v
```

---

## Project layout

```
taskflow/
  .claude-plugin/plugin.json   # Plugin manifest
  .mcp.json                    # MCP server definition
  db/
    init.sql                   # Schema + pipeline seed data
    taskflow.db                # Runtime DB (gitignored)
    audit.log                  # Tool call audit trail (gitignored)
  servers/
    mcp_server.py              # FastMCP server (all tools)
  agents/                      # 6 agent files
  skills/                      # 9 skill directories
  copilot-instructions.md      # Agent routing + tool reference
  hooks.json                   # SessionStart + PostToolUse hooks
  tests/                       # Test suite
```

---

## Blocked tasks

If a task reaches `retry_count = 3` it becomes `blocked`. To unblock:

```sql
-- Reset for another attempt
UPDATE tasks SET retry_count = 0, status = 'pending' WHERE id = <task_id>;
-- Or force-advance
UPDATE tasks SET status = 'done' WHERE id = <task_id>;
```

Then use `/pipeline-status` to see the updated state.
