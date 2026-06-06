# TaskFlow — Copilot Instructions

TaskFlow is a database-driven agentic pipeline for software development. Every agent action is authorised and scoped by a task record in a SQLite database. The database is the single source of truth for pipeline state, permissions, and context.

---

## Core rules

1. **No task → no action.** An agent may only act when it has claimed a task.
2. **Claim before submitting.** Always call `claim_task` before calling any `submit_*` tool.
3. **No delete tools exist.** There are none. Do not attempt deletion.
4. **Context is scoped.** `read_task_context` returns only records relevant to the current task. Do not query other tools for records outside your task's scope.

---

## Agent routing

| Agent | Invoke when... |
|---|---|
| **TaskFlow Product Manager** | Step 3 (define features), step 10 (decisions), step 12 (decision artefacts) |
| **TaskFlow PM Reviewer** | Step 2 (review project), step 4 (review features), step 11 (review decisions), step 13 (final verification) |
| **TaskFlow Tester** | Step 5 (write test specs), step 8 (run tests) |
| **TaskFlow Test Reviewer** | Step 6 (review test specs) |
| **TaskFlow Builder** | Step 7 (build) |
| **TaskFlow Documenter** | Step 9 (retrospective) |

Use `/my-tasks` to see which tasks are pending and which agent to invoke.

---

## Pipeline overview

### Pre-cycle setup (run once via `/start-project`)

`/start-project` → creates project record + seeds step-3 task

### Repeating cycle (steps 3–13, per feature from step 5)

| Step | Agent | Output | Gate |
|---|---|---|---|
| 3 | product_manager | Features + DoD | → step 4 |
| 4 | pm_reviewer | Feature approval | → step 5 × N features |
| 5 | tester | Test specs | → step 6 |
| 6 | test_reviewer | Test spec approval | → step 7 |
| 7 | builder | Build report | → step 8 |
| 8 | tester | Test results | loop until pass → step 9 |
| 9 | documenter | Retro + recommendations | auto → step 10 |
| 10 | product_manager | Decisions | → step 11 |
| 11 | pm_reviewer | Decision approval | → step 12 |
| 12 | product_manager | Decision artefacts | → step 13 |
| 13 | pm_reviewer | Final verification | → step 3 (next cycle) |

---

## MCP tool reference

### Universal (all agents)

| Tool | Purpose |
|---|---|
| `read_pending_tasks(agent_role)` | List pending tasks for a role |
| `claim_task(task_id)` | Mark task in_progress; must be called before any submit |
| `read_task_context(task_id)` | Load scoped records for this task |

### Slash command tools

| Tool | Purpose |
|---|---|
| `start_project(name, brief_text, brief_path?)` | Create project + seed step-3 task |
| `list_projects()` | List all projects with task summaries |
| `read_pipeline_status(project_id)` | Full pipeline state grouped by status |

### Product manager

| Tool | Purpose |
|---|---|
| `submit_project(task_id, name, brief_text, brief_path?)` | Create project record (step 1) |
| `submit_features(task_id, features[])` | Create features + DoD (step 3) |
| `read_backlog(project_id)` | Read pending backlog items |
| `promote_backlog_item(backlog_id, title, description, ...)` | Promote backlog item to active feature |
| `submit_decisions(task_id, decisions[])` | Create decisions; new_feature type → backlog (step 10) |
| `submit_decision_artefact(task_id, decision_id, artefact_type, title, content)` | Record a pattern/gotcha/note (step 12) |
| `complete_decisions_task(task_id)` | Mark step-12 done; spawns step-13 |

### Tester

| Tool | Purpose |
|---|---|
| `submit_test_specs(task_id, specs[])` | Create test specs (step 5) |
| `submit_test_results(task_id, results[])` | Record test pass/fail; auto-advances or retries (step 8) |

### Builder

| Tool | Purpose |
|---|---|
| `submit_build_report(task_id, summary, issues?, wins?, notes?)` | Create build report (step 7) |

### Documenter

| Tool | Purpose |
|---|---|
| `submit_retro(task_id, summary, recommendations[])` | Create retro; auto-spawns step-10 (step 9) |

### Reviewer (pm_reviewer, test_reviewer)

| Tool | Purpose |
|---|---|
| `approve_task(task_id, notes?)` | Approve task; cascades next step(s) |
| `reject_task(task_id, notes)` | Reject task; re-creates worker task with feedback |

---

## Rejection and retry rules

- Worker step rejections re-create the same step with `rejection_notes` injected.
- Reviewer step rejections re-create the preceding worker step.
- Step-8 (test loop): failures increment `retry_count`. At `retry_count = 3` the task becomes `blocked`.
- Blocked tasks require human intervention: reset `retry_count` in the DB or override `status`.

---

## Backlog and cycle restart

- Decisions with `decision_type = 'new_feature'` write to `feature_backlog` (status: `pending`).
- Step 13 approval spawns a new step-3 task — the next cycle begins automatically.
- In step 3, the PM calls `read_backlog` and `promote_backlog_item` to bring backlog items into the cycle.

---

## Skill routing (agents)

After calling `read_task_context`, check `step_number` and invoke the matching skill:

| Step | Skill |
|---|---|
| 3 | `write-features` |
| 5 | `write-test-specs` |
| 6 | `review-tests` |
| 8 | `run-tests` |
| 9 | `write-retro` |
| 10, 12 | `write-decisions` |

---

## File access policy

| Agent | Files |
|---|---|
| builder | Read + write |
| tester | Read + write |
| product_manager | Read-only |
| pm_reviewer | Read-only |
| test_reviewer | Read-only |
| documenter | None |
