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

There are two ways to kick off a project. Use **Path A** when a client has filled in the brief form; use **Path B** for quick or informal starts.

### Path A — Project brief form (recommended)

1. Open `docs/project-brief-form.html` in any browser (no server needed — it runs offline).
2. Complete all sections: identity, goals, features, workflows, NFRs, integrations, risks, timeline.
3. Click **Generate brief** — a `project-brief-<name>.json` file downloads to your machine.
4. In VS Code Copilot chat, invoke the **TaskFlow Product Manager** agent and run:

   ```
   @TaskFlow Product Manager use ingest_brief to start this project
   ```

   Paste the contents of the JSON file (or provide the file path). The agent calls `ingest_brief`, which parses all structured data into the database and seeds the first pipeline task.
5. Proceed to [Working the pipeline](#working-the-pipeline).

### Path B — Free-text brief

1. Open a project workspace in VS Code.
2. Type `/start-project` in Copilot chat and follow the prompts — paste a brief as plain text or provide a file path.
3. Proceed to [Working the pipeline](#working-the-pipeline).

### Working the pipeline

1. Use `/my-tasks` to see what each agent needs to do next.
2. Invoke the appropriate agent (e.g. **TaskFlow Product Manager**) to work the next task.
3. Use `/pipeline-status` at any time to see the full pipeline state.

---

## Slash commands

| Command | Description |
|---|---|
| `/start-project` | Start a project from free-text or a brief file (Path B) |
| `/my-tasks` | Show pending tasks for a chosen agent role |
| `/pipeline-status` | Show the full pipeline state for a project |

For the form-based path, use `ingest_brief` directly via the **TaskFlow Product Manager** agent rather than `/start-project`.

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

## Project brief form

The brief form (`docs/project-brief-form.html`) is a single offline HTML file — no install, no server, no dependencies.

**Sections captured:**

| Section | What agents use it for |
|---|---|
| Project identity & problem | All agents — project scope and context |
| Goals & success metrics | PM (step 3 feature alignment); PM Reviewer (step 13 final verification) |
| User roles & workflows | PM (step 3 user-centric features); Tester (step 5 test scenario design) |
| Features (Must / Should / Could) | PM (step 3 starting point — promote Must features first) |
| Non-functional requirements | Builder (step 7 implementation constraints); Tester (step 8 verification) |
| Integrations | Builder (step 7 — system, direction, auth method, phase 1 flag) |
| Risks | PM (steps 10/12 — seeded as initial decision artefacts) |
| Release phases | PM (step 3 — assigns features to cycles) |
| Timeline & deadline | All reviewers — context for prioritisation |

**Form features:**

- Dynamic add/remove rows for features, roles, workflows, integrations, risks
- Toggle rows for NFRs — only enabled constraints are stored; disabled ones produce no noise in the DB
- Client-side validation before download (required fields, at least one Must feature, at least one platform)
- Auto-saves to `localStorage` every 2 seconds — reload the page and it offers to restore the draft
- Downloads as `project-brief-<slug>.json` — the file is the pipeline entry artefact

---

## Pipeline overview

```
docs/project-brief-form.html  →  project-brief.json  →  ingest_brief
          OR
/start-project (free-text)
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

**Pipeline tables** (populated by agents during the cycle):

| Table | Purpose |
|---|---|
| `pipeline_steps` | 13-step workflow definition (seed data) |
| `projects` | Project records with scalar brief fields + raw JSON |
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

**Brief-derived tables** (populated by `ingest_brief` from the form JSON):

| Table | Purpose |
|---|---|
| `project_outcomes` | Stated goals from the brief |
| `success_metrics` | Measurable targets for step-13 verification |
| `user_roles` | Actor descriptions and primary workflows |
| `stakeholders` | Named stakeholders and their authority |
| `key_workflows` | Actor → trigger → steps → outcome journeys |
| `non_functional_requirements` | Enabled NFR constraints only (performance, security, etc.) |
| `integrations` | External systems with direction, auth method, phase flag |
| `project_risks` | Risks with likelihood/impact/mitigation |
| `release_phases` | Phase-by-phase scope and target dates |
| `brief_features` | Feature suggestions from the form (PM refines these at step 3) |

All brief-derived tables are returned by `read_task_context` via the `brief` key — agents never need to re-read the JSON file.

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
  docs/
    project-brief-form.html    # Offline brief form → downloads project-brief.json
    project-brief-template.md  # Reference template
  db/
    init.sql                   # Schema + pipeline seed data
    taskflow.db                # Runtime DB (gitignored)
    audit.log                  # Tool call audit trail (gitignored)
  servers/
    mcp_server.py              # FastMCP server (all tools incl. ingest_brief)
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
