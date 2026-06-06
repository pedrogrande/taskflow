---
name: my-tasks
description: Show pending tasks for a TaskFlow agent role. Lists what work is waiting and which project/feature each task belongs to. Use this to find out what an agent needs to do next.
argument-hint: "[agent role: product_manager | pm_reviewer | tester | test_reviewer | builder | documenter]"
disable-model-invocation: true
---

# Show pending tasks for an agent role

## Steps

1. If the user provided a role in their message, use it. Otherwise ask:
   "Which agent role? Choose from: `product_manager`, `pm_reviewer`, `tester`, `test_reviewer`, `builder`, `documenter`"

2. Call `read_pending_tasks(agent_role=...)`.

3. If no tasks are returned:
   - Say "No pending tasks for `{role}` right now."
   - Suggest calling `/pipeline-status` to see overall pipeline state.

4. If tasks are returned, format them as a table:

   | Task ID | Step | Step Name | Feature | Retry |
   |---------|------|-----------|---------|-------|
   | 42      | 5    | Write test specs | Feature A | 0 |

5. Tell the user which agent to invoke for each task:
   - "To work on these tasks, invoke the **TaskFlow {RoleName}** agent."
