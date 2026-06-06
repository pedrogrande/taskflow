---
name: pipeline-status
description: Show the current state of the TaskFlow pipeline for a project — which steps are done, in progress, pending, blocked, or rejected. Use this to get an overview of where the pipeline stands.
argument-hint: "[project ID or leave blank to list projects]"
disable-model-invocation: true
---

# Show pipeline status for a project

## Steps

1. If the user provided a project ID, use it. Otherwise:
   - Call `list_projects()` and display the results so the user can pick one.
   - Ask "Which project ID?"

2. Call `read_pipeline_status(project_id=...)`.

3. Display the project name and status.

4. Display features (if any).

5. Display tasks grouped by status using this format:

### In Progress

| Task ID | Step | Step Name | Agent | Feature |
|---------|------|-----------|-------|---------|

### Pending

| Task ID | Step | Step Name | Agent | Feature |
|---------|------|-----------|-------|---------|

### Blocked ⚠️

| Task ID | Step | Step Name | Agent | Feature | Retry # |
|---------|------|-----------|-------|---------|---------|

### Done

_(count only — e.g. "12 tasks completed")_

1. If there are blocked tasks, highlight them and suggest:
   "Blocked tasks require human intervention. You can reset `retry_count` or override `status` directly in the DB, or invoke the relevant agent to assess the situation."

2. If the pipeline looks stalled (no in-progress, no pending), suggest `/my-tasks` to check each role.
