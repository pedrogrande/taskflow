# TaskFlow — Implementation Plan

**Status:** Draft  
**Date:** 2026-06-06  
**Architecture ref:** ARCHITECTURE.md

---

## Deliverables

| # | Deliverable | Phase |
|---|---|---|
| 1 | `db/init.sql` — schema + 13-step seed data | 1 |
| 2 | `servers/mcp_server.py` — universal tools | 1 |
| 3 | PM tools + cascade for steps 1–4 | 2 |
| 4 | `product_manager.agent.md`, `pm_reviewer.agent.md` | 2 |
| 5 | Tester + builder tools + cascade for steps 5–8 | 3 |
| 6 | `tester.agent.md`, `test_reviewer.agent.md`, `builder.agent.md` | 3 |
| 7 | Test loop retry logic (`retry_count`, `blocked`) | 3 |
| 8 | Documenter + PM decision tools + cascade for steps 9–13 | 4 |
| 9 | `documenter.agent.md` + step-13 cycle restart | 4 |
| 10 | Slash command skills (`start-project`, `my-tasks`, `pipeline-status`) | 5 |
| 11 | Step-specific auto-loaded skills (write-features, write-test-specs, etc.) | 5 |
| 12 | Hooks (`SessionStart` inject, `PostToolUse` audit) | 6 |
| 13 | Plugin manifest, `copilot-instructions.md`, `README.md` | 6 |
| 14 | Test suite | 6 |

---

## Phase 1 — Foundation

**Goal:** DB, MCP server skeleton, plugin wired up and pingable.

### Steps

1. Create directory structure: `db/`, `servers/`, `agents/`, `skills/`, `.claude-plugin/`
2. Write `db/init.sql`:
   - All 13 tables (pipeline_steps, projects, features, definitions_of_done, test_specs, build_reports, test_results, retro_reports, recommendations, decisions, decision_artefacts, feature_backlog, tasks)
   - `pipeline_steps` seed data: all 13 steps with `agent_role`, `requires_approval`, `on_approval_spawn` values
   - All FK constraints + CHECK constraints
3. Write `servers/mcp_server.py` (PEP 723 inline deps, `mcp>=1.2.0`):
   - `_ensure_db()` runs init.sql
   - Universal tools: `read_pending_tasks`, `read_task_context`, `claim_task`
   - `read_task_context` scoping: step-number lookup → return correct record set per §4 of ARCHITECTURE.md
4. Write `.claude-plugin/plugin.json` and `.mcp.json` (uv run, DB_PATH env var)
5. Write `.gitignore` (ignore `db/taskflow.db`, `db/audit.log`)

**Done when:** MCP server starts, `read_pending_tasks('product_manager')` returns an empty list without errors.

---

## Phase 2 — PM workflow (steps 1–4)

**Goal:** A product manager can ingest a brief, define features, and a pm_reviewer can approve through to step 5 being spawned.

### Steps

1. Add PM worker tools: `submit_project`, `submit_features`, `read_backlog`, `promote_backlog_item`
2. Add reviewer tools: `approve_task`, `reject_task`
3. Implement `approve_task` cascade logic:
   - Step 2 approval → spawn step 3
   - Step 4 approval → spawn one step-5 task **per feature** (`per_feature` spawn type)
4. Implement `reject_task` logic: re-create the preceding worker task with `rejection_notes`, increment `retry_count`, block at 3
5. Write `agents/product_manager.agent.md` — PM tools + read-only file tools + body pointing to step-specific skills
6. Write `agents/pm_reviewer.agent.md` — reviewer tools only + read-only file tools

**Done when:** Full steps 1–4 flow executes in DB; approving step-4 task with 2 features creates 2 step-5 tasks.

---

## Phase 3 — Tester + builder workflow (steps 5–8)

**Goal:** Test specs written and reviewed; build and test loop runs; loop blocks at retry limit.

### Steps

1. Add tester tools: `submit_test_specs`, `submit_test_results`
   - `submit_test_results`: if all results pass → mark task done + spawn step 9
   - If any fail → increment `retry_count`; if `retry_count >= 3` → set `status = 'blocked'`; else re-create step-8 task
2. Add builder tool: `submit_build_report`
3. Extend `approve_task` cascade:
   - Step 6 approval → spawn step 7
   - Step 7 (builder) has `requires_approval = 1` → pm_reviewer approves → spawn step 8
4. Write `agents/tester.agent.md` — tester tools + read+write file tools
5. Write `agents/test_reviewer.agent.md` — reviewer tools + read-only file tools
6. Write `agents/builder.agent.md` — builder tools + full file tools (edit, terminal read)

**Done when:** Step-8 task rejects twice, increments retry_count; third rejection sets status=blocked. Passing submission spawns step 9.

---

## Phase 4 — Documenter + decisions + cycle restart (steps 9–13)

**Goal:** Retro through decisions reviewed; step 13 approval spawns new step 3 (cycle restart).

### Steps

1. Add documenter tool: `submit_retro`
   - On completion, directly spawn step-10 task (no approval gate — auto-advance)
2. Add PM tools: `submit_decisions`, `submit_decision_artefact`
   - `submit_decisions`: any decision with `type = 'new_feature'` → insert `feature_backlog` row
3. Extend `approve_task` cascade:
   - Step 9: `requires_approval = 0` → handled by `submit_retro` auto-spawn
   - Step 11 approval → spawn step 12
   - Step 13 approval → spawn new **step-3** task (cycle restart, not pipeline complete)
4. Write `agents/documenter.agent.md` — documenter tools only, no file tools

**Done when:** Full 13-step flow executes; step-13 approval creates a new step-3 pending task; feature_backlog rows exist for new-feature decisions.

---

## Phase 5 — Skills

**Goal:** All slash commands and auto-loaded step skills present and correct.

### Steps

1. Write slash command skills:
   - `skills/start-project/SKILL.md` — prompts for brief (file or text), calls `submit_project`, seeds step-1 task; sets `task_data` with file path if provided
   - `skills/my-tasks/SKILL.md` — calls `read_pending_tasks` for a chosen role, presents list
   - `skills/pipeline-status/SKILL.md` — queries tasks by project, renders step-by-step status
2. Write auto-loaded step skills:
   - `skills/write-features/SKILL.md` — feature + DoD format, quality criteria
   - `skills/write-test-specs/SKILL.md` — spec structure, coverage expectations, rationale format
   - `skills/review-tests/SKILL.md` — what to verify (spec ↔ DoD alignment, testability, expected results)
   - `skills/run-tests/SKILL.md` — how to execute, how to record pass/fail, what counts as a pass
   - `skills/write-retro/SKILL.md` — retro format, recommendation types, gotcha documentation
   - `skills/write-decisions/SKILL.md` — decision types, artefact format, backlog entry rules

**Done when:** `/start-project`, `/my-tasks`, `/pipeline-status` appear as slash commands; auto-loaded skills appear in agent diagnostics.

---

## Phase 6 — Hooks, instructions, tests

**Goal:** Plugin is production-ready: auditable, documented, tested.

### Steps

1. Write `.github/hooks/taskflow.json` (or `hooks.json` in plugin root per plugin spec):
   - `SessionStart`: shell script calls `read_pending_tasks` via sqlite3 and outputs `additionalContext`
   - `PostToolUse`: shell script appends `[timestamp] tool=X task_id=Y role=Z` to `db/audit.log`
2. Write `copilot-instructions.md` — agent routing table, tool reference, pipeline step summary, cycle restart rule, retry/blocked rules
3. Write `README.md` — prerequisites (uv), install, `/start-project` quickstart, pipeline overview, schema summary, test instructions
4. Write test suite (`tests/`):
   - Schema tests: all tables, FKs, CHECK constraints, idempotency
   - MCP tool tests: all tools, cascade logic, retry/block logic, per_feature spawn, cycle restart, `feature_backlog` population, `decision_artefacts` creation
5. Run tests; fix failures

**Done when:** All tests pass; plugin installs cleanly; full 13-step cycle runs end-to-end in a test workspace.

---

## Constraints

- Use `uv run` + PEP 723 inline deps (same pattern as devflow-db)
- No external scheduler — all cascade logic is synchronous inside `approve_task` and `submit_retro`
- No delete tools exposed
- SQLite FK enforcement via `PRAGMA foreign_keys = ON` per connection
- Plugin directory: `/Users/peteargent/edgeos/devflow/taskflow/`
