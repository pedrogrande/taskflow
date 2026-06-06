---
name: write-decisions
description: Guide for the product_manager agent when writing decision records (step 10) and decision artefacts (step 12). Auto-loaded when a step-10 or step-12 task is in progress.
user-invocable: false
---

# Writing decisions and artefacts (Steps 10 & 12)

## Step 10 — Writing decisions

You are converting recommendations into concrete decisions. Each recommendation needs a decision.

### Decision types

| Type | Meaning |
|---|---|
| `implement` | This will be built in the current or next cycle |
| `new_feature` | This spawns a backlog item for a future cycle |
| `defer` | Acknowledged but not actioned now |
| `reject` | Decided against; document why in `rationale` |
| `document` | No code change needed — document the finding only |

### For `new_feature` decisions, also provide

- `backlog_title`: Short name for the backlog item
- `backlog_description`: Enough detail for the PM to evaluate it in a future cycle
- `priority`: Integer 0–10 (higher = more urgent)

### Calling submit_decisions

```
submit_decisions(
  task_id=<your task id>,
  decisions=[
    {
      "recommendation_id": 4,
      "decision": "Implement Redis cache for /api/products in the next cycle",
      "decision_type": "implement",
      "rationale": "Response times exceeded 500ms under load in the retro"
    },
    {
      "recommendation_id": 5,
      "decision": "Admin dashboard",
      "decision_type": "new_feature",
      "rationale": "Operations team needs visibility into active orders",
      "backlog_title": "Admin order dashboard",
      "backlog_description": "Read-only dashboard showing live order status for ops team",
      "priority": 7
    }
  ]
)
```

---

## Step 12 — Writing decision artefacts

You are capturing the *learnings* from implementing the decisions in step 11. These are patterns, gotchas, and constraints discovered during the process.

### Artefact types

| Type | When to use |
|---|---|
| `pattern` | A reusable approach that worked well and should be repeated |
| `gotcha` | A trap or non-obvious behaviour to avoid in future |
| `note` | Useful context that doesn't fit the other types |
| `constraint` | A technical or business constraint uncovered during implementation |
| `other` | Anything else worth preserving |

### Calling submit_decision_artefact (once per artefact)

```
submit_decision_artefact(
  task_id=<your task id>,
  decision_id=<the relevant decision id>,
  artefact_type="pattern",
  title="Cache-aside for read-heavy endpoints",
  content="Use cache-aside pattern (read-through on miss) for all GET endpoints with >100 req/min. TTL = 60s. Invalidate on write."
)
```

Call this once per artefact. When all artefacts are submitted, call:

```
complete_decisions_task(task_id=<your task id>)
```

This marks the task done and spawns step-13 for the pm_reviewer.

### Minimum artefacts

Submit at least one artefact if any of these are true:

- A pattern was discovered that should be reused
- Something non-obvious caused a problem (gotcha)
- A constraint was identified that future features must respect

If nothing notable was discovered, submit a single `note` artefact acknowledging the clean implementation.
