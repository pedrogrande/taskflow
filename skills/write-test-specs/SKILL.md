---
name: write-test-specs
description: Guide for the tester agent when writing test specs in step 5. Auto-loaded when a step-5 task is in progress.
user-invocable: false
---

# Writing test specs (Step 5)

You are writing test specifications before any implementation begins. Your specs define what "passing" means for this feature.

## Before you start

- Read the `feature` and `definitions_of_done` from your task context.
- Each DoD criterion must have at least one test spec. Aim for 1–3 specs per criterion.
- Check `rejection_notes` on your task if present — the previous submission was rejected.

## Test spec structure

Each spec requires:

- **description**: One sentence — what is being tested? (e.g. "Valid login returns a session token")
- **expected_result**: Exact, measurable outcome (e.g. "HTTP 200 with `{token: string}` in body")

Optional but recommended:

- **rationale**: Why this test matters — which DoD criterion does it cover?
- **strategy**: How it will be tested — unit, integration, e2e, contract?
- **order_index**: Execution order hint

## Coverage rules

- Every DoD criterion must map to at least one spec.
- Test both the happy path **and** at least one error/edge case per criterion.
- Do not write duplicate specs for the same behaviour.

### Good spec examples

```
description: "POST /users with valid payload creates user and returns 201"
expected_result: "HTTP 201, body contains {id: integer, email: string}"
rationale: "Covers DoD: user creation endpoint returns correct response"
strategy: "integration"
```

```
description: "POST /users with missing email returns 400"
expected_result: "HTTP 400, body contains {error: 'email is required'}"
rationale: "Covers DoD: invalid input is rejected with a clear error"
strategy: "integration"
```

### Bad spec examples

- `expected_result: "no errors"` ← not measurable
- `description: "it works"` ← not specific
- `expected_result: "200"` ← missing body/content assertion

## Calling submit_test_specs

```
submit_test_specs(
  task_id=<your task id>,
  specs=[
    {
      "description": "...",
      "expected_result": "...",
      "rationale": "...",
      "strategy": "integration",
      "order_index": 0
    },
    ...
  ]
)
```
