# TaskFlow — Architecture & Design Document

**Status:** Draft — OQs 1–6 resolved  
**Date:** 2026-06-06  
**Author:** Pete Argent

---

## 1. Vision

TaskFlow is a VS Code agent plugin that implements a **database-driven agentic pipeline** for software development. Every action an agent takes is authorised and scoped by a task record. The database is the single source of truth for pipeline state, permissions, and context. File access is granted only to agents whose role requires it, and is restricted to the minimum needed.

### Core principles

| Principle | What it means in practice |
|---|---|
| **Task as permission** | An agent may only act when it has been assigned a task. No task → no action. |
| **Minimum viable context** | Each agent receives only the records its current task requires — nothing from other features, pipeline stages, or projects. |
| **Tool as boundary** | What a tool exposes is the only thing an agent can read or write. Tool presence/absence is enforced at the MCP layer. |
| **Pipeline as data** | The workflow definition lives in the database, making it inspectable, auditable, and eventually configurable. |

---

## 2. How the pipeline works

The pipeline is a sequence of **steps**. Each step defines:

- Which agent role performs it
- What output record(s) it must produce
- Whether a reviewer must approve it before the pipeline advances
- What step(s) to spawn on approval
- What to do on rejection (retry the same step, or route to a repair step)

When a reviewer approves a step, the MCP server's `approve_task` tool immediately creates the next task record(s) in the same database transaction. There is no external scheduler, background process, or polling. The cascade is synchronous code inside the MCP tool.

### Pipeline steps (v1)

| # | Agent role | Work output | Needs approval | On approval → |
|---|---|---|---|---|
| 1 | `product_manager` | Project record + requirements | yes | step 2 |
| 2 | `pm_reviewer` | Approval of project record | — | step 3 |
| 3 | `product_manager` | Feature records + definitions of done | yes | step 4 |
| 4 | `pm_reviewer` | Approval of features + DoD | — | step 5 × N features |
| 5 | `tester` | Test spec records | yes | step 6 |
| 6 | `test_reviewer` | Approval of test specs | — | step 7 |
| 7 | `builder` | Build report record | yes | step 8 |
| 8 | `tester` | Test result records | loop until pass | step 9 |
| 9 | `documenter` | Retro report + recommendation records | — | step 10 (auto) |
| 10 | `product_manager` | Decision records | yes | step 11 |
| 11 | `pm_reviewer` | Approval of decisions | — | step 12 |
| 12 | `product_manager` | Decision artefacts + backlog entries | yes | step 13 |
| 13 | `pm_reviewer` | Final verification | — | step 3 (next cycle) |

**Steps 1–2 are pre-cycle setup**, run once when `/start-project` is called. Step 2 approval spawns step 3, beginning the first cycle. **Steps 3–13 form the repeating cycle** — step 13 approval spawns a new step-3 task. Steps 5–13 run **per feature**. Approving step 4 spawns one step-5 task per feature record.

### Pipeline cycle and backlog

Steps 1–2 run once when `/start-project` is called. They create and approve the project record. Step 2 approval spawns step 3 — the start of the first cycle.

Step 13 is not the end of the system — it ends one cycle and immediately spawns a new step-3 task. Decisions from step 10, and artefacts created in step 12, inform the next cycle. New-feature decisions are written to `feature_backlog` in step 12. When step 3 runs for cycle 2+, the PM reads both the requirements table and the backlog, and decides which items to promote into the active feature set. This gives the PM deliberate scope control each cycle and prevents mid-flight expansion.

### Rejection behaviour

| Step type | On rejection |
|---|---|
| Worker step (1, 3, 5, 7, 10, 12) | Re-create the same step task with `rejection_notes` injected |
| Reviewer step (2, 4, 6, 11, 13) | Same — re-create the preceding worker step |
| Test loop (8) | Re-create step 8 task (tester retries against same build) |

---

## 3. Schema

```
pipeline_steps
  id                      INTEGER PK
  step_number             INTEGER UNIQUE NOT NULL
  name                    TEXT NOT NULL
  agent_role              TEXT NOT NULL        -- enforced enum
  output_record_type      TEXT NOT NULL        -- what this step produces
  requires_approval       INTEGER NOT NULL DEFAULT 1  -- 0 = no review gate
  on_approval_spawn       TEXT                -- JSON: [step_number, ...] or "per_feature"
  on_rejection_action     TEXT NOT NULL DEFAULT 'retry'  -- 'retry' | 'repair_step'
  repair_step_number      INTEGER             -- nullable; used when on_rejection_action = 'repair_step'

projects
  id · name · brief_text · brief_path · status · created_at
  -- brief_text: full brief content; copied in at project creation for agents without file access
  -- brief_path: original file path (nullable; null if project initiated from chat text)

features
  id · project_id · title · description · source_requirement_text · order_index

definitions_of_done
  id · feature_id · criterion · verifiable   -- verifiable: 1|0

test_specs
  id · feature_id · description · rationale · strategy · expected_result · order_index

build_reports
  id · feature_id · summary · issues · wins · notes · created_at

test_results
  id · test_spec_id · build_report_id · passed · notes · created_at

retro_reports
  id · feature_id · summary · created_at

recommendations
  id · retro_report_id · description · recommendation_type

decisions
  id · recommendation_id · decision · rationale · created_at

decision_artefacts
  id · decision_id · artefact_type · title · content · created_at
  -- artefact_type: 'pattern' | 'gotcha' | 'note' | 'constraint' | 'other'
  -- PM inserts these in step 12 to capture patterns, gotchas, and other learnings

feature_backlog
  id · project_id · title · description · source_decision_id · source_recommendation_id
  priority · status · created_at
  -- Status: 'pending' | 'promoted' | 'deferred' | 'rejected'
  -- Populated by submit_decisions when decision_type = 'new_feature'
  -- PM reads this in step 3 alongside requirements table

tasks
  id · project_id · feature_id (nullable) · step_id REFERENCES pipeline_steps(id)
  agent_role · status · rejection_notes · retry_count · task_data · created_at · completed_at
  -- retry_count: incremented on each rejection; task becomes 'blocked' at 3
  -- task_data: JSON blob for step-specific input (e.g. brief file path for step 1)

  CHECK status IN ('pending', 'in_progress', 'done', 'rejected', 'blocked')
```

All foreign keys enforced. No delete tools exposed.

---

## 4. MCP tool design

Tools are grouped by agent role. Each agent file declares only the tools it is permitted to call.

### Universal tools (all agents)

| Tool | Description |
|---|---|
| `read_pending_tasks(agent_role)` | Returns tasks with `status = 'pending'` for the caller's role |
| `read_task_context(task_id)` | Returns **only** the records scoped to this task — determined by the task's `step_id` and `feature_id` |
| `claim_task(task_id)` | Sets `status = 'in_progress'`; prevents two agents claiming the same task |

### Worker tools

| Tool | Agent role | Creates |
|---|---|---|
| `submit_project(task_id, name, brief_text)` | `product_manager` | `projects` row; marks task done; no auto-advance (approval needed) |
| `submit_features(task_id, features[])` | `product_manager` | `features` + `definitions_of_done` rows |
| `submit_test_specs(task_id, specs[])` | `tester` | `test_specs` rows |
| `submit_build_report(task_id, ...)` | `builder` | `build_reports` row |
| `submit_test_results(task_id, results[])` | `tester` | `test_results` rows; if all pass, marks task done |
| `submit_retro(task_id, summary, recommendations[])` | `documenter` | `retro_reports` + `recommendations` rows; **directly spawns step-10 task** (no approval gate) |
| `submit_decisions(task_id, decisions[])` | `product_manager` | `decisions` rows; any decision with `type = 'new_feature'` also inserts a `feature_backlog` row |
| `submit_decision_artefact(task_id, decision_id, artefact_type, title, content)` | `product_manager` | `decision_artefacts` row — captures patterns, gotchas, notes, or constraints discovered during step 12 |
| `read_backlog(project_id)` | `product_manager` | Returns `feature_backlog` rows with `status = 'pending'` for PM review in step 3 |
| `promote_backlog_item(backlog_id, feature_data)` | `product_manager` | Converts a backlog item to a `features` row; sets backlog status to `'promoted'` |

### Reviewer tools

| Tool | Agent role | Effect |
|---|---|---|
| `approve_task(task_id, notes)` | `pm_reviewer`, `test_reviewer` | Marks task approved; spawns next task(s) per pipeline definition |
| `reject_task(task_id, notes)` | `pm_reviewer`, `test_reviewer` | Marks task rejected; re-creates the worker task with `rejection_notes` |

### Cascade logic (inside `approve_task`)

```
1. Mark current task status = 'done'
2. Look up pipeline_steps.on_approval_spawn for this step
3. If spawn = [step_numbers]:
     insert one task row per step_number
4. If spawn = "per_feature":
     insert one task row per feature in this project
5. Return: approved task + list of newly created task IDs
```

All of this is one database transaction. If the insert fails, the approval rolls back.

### `read_task_context` scoping rules

| Task step | Records returned |
|---|---|
| Step 1 (ingest brief) | Brief file path stored in task context — PM reads the file directly using VS Code file-read tools |
| Step 2 (review project) | Project record only |
| Step 3 (define features) | Project record + requirements text + `feature_backlog` items with `status = 'pending'` |
| Step 4 (review features) | Project record + all features + all DoD for this project |
| Step 5 (write tests) | Feature record + DoD records for this feature |
| Step 6 (review tests) | Feature record + DoD records + test specs for this feature |
| Step 7 (build) | Feature + DoD + test specs + project summary |
| Step 8 (run tests) | Feature + DoD + test specs + build report |
| Step 9 (retro) | Build report + test results for this feature |
| Step 10 (decisions) | Retro report + recommendations |
| Step 11 (review decisions) | Retro report + recommendations + decisions |
| Step 12 (implement decisions) | Decisions + their review notes + existing `decision_artefacts` for this feature |
| Step 13 (verify decisions) | Decisions + `decision_artefacts` + `feature_backlog` entries created in step 12 |

---

## 5. Agent files

Each agent is a `.agent.md` file with YAML frontmatter. The agent file declares its role, MCP tools, and VS Code built-in tools. Tool presence is the sole enforcement mechanism — agents cannot access anything not on their list.

### File access policy

| Agent | MCP tools | VS Code file tools |
|---|---|---|
| `product_manager` | All PM tools | Read-only (`search/codebase`, file read) |
| `pm_reviewer` | `approve_task`, `reject_task`, `read_task_context`, `read_pending_tasks`, `claim_task` | Read-only (`search/codebase`, file read) |
| `tester` | All tester tools | Read + write (needs to read code to write tests, write test files) |
| `test_reviewer` | `approve_task`, `reject_task`, `read_task_context`, `read_pending_tasks`, `claim_task` | Read-only — reads code and test files to validate test specs against implementation |
| `builder` | All builder tools | Read + write (`edit`, `search/codebase`, `search/usages`, terminal read) |
| `documenter` | All documenter tools | None — produces DB records only |

> **Note on VS Code tool names:** Exact tool identifiers (`edit`, `search/codebase`, etc.) follow the VS Code custom agent spec. The `tools` array in each `.agent.md` frontmatter must list both the MCP server tools (`taskflow/*`) and any VS Code built-in tools the agent needs. Tool names not available in the active session are silently ignored per the VS Code spec.

### Agent files

| File | Role |
|---|---|
| `agents/product_manager.agent.md` | `product_manager` |
| `agents/pm_reviewer.agent.md` | `pm_reviewer` |
| `agents/tester.agent.md` | `tester` |
| `agents/test_reviewer.agent.md` | `test_reviewer` |
| `agents/builder.agent.md` | `builder` |
| `agents/documenter.agent.md` | `documenter` |

---

## 6. Slash commands (skills)

| Command | What it does |
|---|---|
| `/start-project` | Prompts for a brief (file path or inline text); stores content in `projects.brief_text` (and path in `projects.brief_path` if a file); seeds steps 1–2 as pre-cycle setup tasks. Step 2 approval then spawns step 3, beginning the first cycle. Subsequent cycles start automatically when step 13 is approved. |
| `/my-tasks` | Calls `read_pending_tasks` for a chosen role; presents the task list so the user can invoke the right agent. |
| `/pipeline-status` | Shows the current pipeline state for a project: which steps are done, in-progress, pending, or rejected. |

---

## 7. Plugin structure

```
taskflow/
  .claude-plugin/
    plugin.json              # Plugin manifest
  .mcp.json                  # MCP server definition
  db/
    init.sql                 # Schema + pipeline_steps seed data
    taskflow.db              # Runtime DB (gitignored)
  servers/
    mcp_server.py            # FastMCP server
    requirements.txt         # mcp>=1.2.0 (uv run via PEP 723 inline)
  agents/
    product_manager.agent.md
    pm_reviewer.agent.md
    tester.agent.md
    test_reviewer.agent.md
    builder.agent.md
    documenter.agent.md
  skills/
    start-project/SKILL.md
    my-tasks/SKILL.md
    pipeline-status/SKILL.md
  copilot-instructions.md
  README.md
  .gitignore
```

---

## 8. Decisions

### OQ-1 — Reviewer interaction model

**Decision: A — reviewer agent runs autonomously.**

Reviewer agents (`pm_reviewer`, `test_reviewer`) execute without human confirmation. Their approval or rejection is visible in chat output and recorded in the `tasks` table. A human who disagrees with a reviewer decision can invoke the reviewer agent again and override it. Option B (human-gated `/approve` command) is deferred to v2.

---

### OQ-2 — Workspace file access per agent

**Decision: Differentiated file access, enforced via the `tools` frontmatter in each `.agent.md`.**

| Agent | File read | File write |
|---|---|---|
| `builder` | Yes | Yes — writes implementation code |
| `tester` | Yes | Yes — writes test files |
| `product_manager` | Yes | No |
| `pm_reviewer` | Yes | No |
| `test_reviewer` | Yes | No — reads code and test files to validate specs against implementation |
| `documenter` | No | No |

The database remains the source of truth for pipeline state. Workspace files are code and test artefacts — they are the *output* of the pipeline, not its state. The `build_reports` and `test_results` DB records serve as the audit trail for what was produced.

---

### OQ-3 — New features from decisions

**Decision: New features from decisions go to a `feature_backlog` table. The PM promotes backlog items deliberately at the start of the next cycle.**

When the PM submits decisions in step 10 and a decision has `type = 'new_feature'`, the server inserts a row into `feature_backlog` (with `status = 'pending'`) rather than immediately creating a new pipeline task. In step 3 of the next cycle, the PM calls `read_backlog(project_id)` alongside `read_requirements`, reviews what is pending, and explicitly calls `promote_backlog_item` for any items it wants to build. The item moves to `status = 'promoted'` and a `features` row is created. Items not selected remain in the backlog for future cycles.

This prevents the pipeline from expanding mid-flight and gives the PM deliberate scope control each cycle.

---

### OQ-4 — Pipeline step configurability

**Decision: Hardcode for v1.** The 13-step workflow is seeded in `init.sql`. Configurable pipelines are deferred to v2.

---

### OQ-5 — Test loop retry limit

**Decision: Retry limit of 3. Task becomes `blocked` on the fourth failure; human intervention required.**

`tasks.retry_count` is incremented on each rejection. At `retry_count = 3`, `update_task` sets `status = 'blocked'` instead of creating a new retry task. `/pipeline-status` surfaces all blocked tasks. A human unblocks the task by either resetting `retry_count` (if they want the agent to try again) or manually overriding `status` to allow the pipeline to advance.

---

### OQ-6 — Brief file access for the product manager

**Decision: The product manager agent has VS Code read-file access. `/start-project` stores the brief file path in the task context; the PM reads the file directly.**

The `/start-project` skill accepts either a brief file path or inline text. The brief content is always stored in `projects.brief_text`, making it accessible to all agents via `read_task_context` regardless of whether they have file tools. If a file path was provided it is also stored in `projects.brief_path` for traceability. The file path is stored in `tasks.task_data` for the step-1 task so the PM agent can read the file directly using its VS Code file-read tool and submit the full text via `submit_project`. This supports both file-based and chat-initiated project creation.

---

## 9. Hooks and skills strategy

### Hooks

Two hooks are used in v1:

| Hook | Event | Purpose |
|---|---|---|
| `inject-pending-tasks` | `SessionStart` | Calls `read_pending_tasks` for the active agent's role and injects the result as context. The agent knows its workload immediately without being explicitly prompted. |
| `audit-log` | `PostToolUse` | Appends a line to `db/audit.log` recording the tool name, task_id (from tool input), agent role, and timestamp. Provides a human-readable pipeline trace. |

A `PreToolUse` hook (secondary file-write guard) is deferred to v2. The `tools` frontmatter in each `.agent.md` is the primary enforcement mechanism.

### Skills strategy

Each agent file body stays minimal — role identity, operating principles, and the instruction to invoke the relevant skill after reading task context. Step-specific procedural detail lives in skills, keeping agent context lean and instructions composable.

| Skill | Step | Type | Agent |
|---|---|---|---|
| `start-project` | Pre-cycle | Slash command | Human-invoked: prompts for brief, seeds steps 1–2 |
| `my-tasks` | Any | Slash command | Human-invoked: shows pending tasks for a chosen role |
| `pipeline-status` | Any | Slash command | Human-invoked: shows pipeline state for a project |
| `write-features` | 3 | Auto-loaded | `product_manager` — DoD criteria and feature record format |
| `write-test-specs` | 5 | Auto-loaded | `tester` — test spec structure and coverage expectations |
| `review-tests` | 6 | Auto-loaded | `test_reviewer` — what to check when validating specs against implementation |
| `run-tests` | 8 | Auto-loaded | `tester` — how to execute tests and record results |
| `write-retro` | 9 | Auto-loaded | `documenter` — retro format and recommendation types |
| `write-decisions` | 10 | Auto-loaded | `product_manager` — decision types and artefact format |

Skills for reviewer/approval steps (2, 4, 6, 11, 12, 13) are deferred; the agent body instructions are sufficient for those steps.

Each agent body instructs: *"After calling `read_task_context`, check the step number and invoke the matching skill."* This makes skill loading agent-driven and explicit, not dependent solely on Copilot's relevance matching.

---

## 10. What is explicitly out of scope for v1

- Multi-user / multi-agent concurrency (one agent per task at a time is assumed)
- Custom pipeline definitions
- Pipeline branching based on LLM-evaluated conditions
- Integration with external issue trackers (Jira, GitHub Issues)
- A visual pipeline dashboard
- Cross-project dependencies
