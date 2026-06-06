---
name: write-retro
description: Guide for the documenter agent when writing the retrospective report and recommendations in step 9. Auto-loaded when a step-9 task is in progress.
user-invocable: false
---

# Writing the retrospective (Step 9)

You are documenting what happened during the build and test cycle for this feature so the team can learn and improve.

## Before you start

- Read the `build_report` and `test_results` from your task context.
- Note how many test specs passed, how many failed (across all attempts), and what issues the builder reported.

## Retrospective summary

Write 3–5 sentences covering:

- What was built and whether it went smoothly
- Any significant issues encountered during build or test
- What the retry count was (if > 0)
- The overall quality signal from the test results

## Recommendations

Provide at least one recommendation. Use these types:

| Type | When to use |
|---|---|
| `improve` | Something could be done better next time (process, code quality, test coverage) |
| `new_feature` | A new capability emerged as clearly needed |
| `fix` | A bug or gap was found that should be addressed |
| `investigate` | Something needs further research before a decision can be made |
| `defer` | Scope was cut — document what was intentionally left out |
| `close` | Everything went well, no action needed |

Each recommendation description should be specific: what exactly should be improved, fixed, or built?

## Calling submit_retro

```
submit_retro(
  task_id=<your task id>,
  summary="...",
  recommendations=[
    {
      "description": "...",
      "recommendation_type": "improve"
    },
    ...
  ]
)
```

`submit_retro` automatically spawns the step-10 task for the product manager.
