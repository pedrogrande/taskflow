---
name: run-tests
description: Guide for the tester agent when executing tests and recording results in step 8. Auto-loaded when a step-8 task is in progress.
user-invocable: false
---

# Running tests and recording results (Step 8)

You are executing the test suite against the current build and recording pass/fail per spec.

## Before you start

- Read the `feature`, `test_specs`, and `build_report` from your task context.
- Check `rejection_notes` on your task — if tests previously failed, the notes describe what went wrong.
- Review the build report `issues` field for known problems to watch for.

## Execution steps

1. Use `search/codebase` and `read` to locate the relevant test files.
2. Use `runTerminalCommand` to execute the test suite (e.g. `pytest`, `npm test`, `go test ./...`).
3. Map each test output back to the corresponding `test_spec_id` from your context.
4. Record a result for **every** test spec — do not skip or omit any.

## Recording results

Call `submit_test_results` with a result for every spec:

```
submit_test_results(
  task_id=<your task id>,
  results=[
    {
      "test_spec_id": 12,
      "passed": true,
      "notes": "Passed in 42ms"
    },
    {
      "test_spec_id": 13,
      "passed": false,
      "notes": "Expected HTTP 401, got 200. Auth middleware not applied to /api/orders."
    },
    ...
  ]
)
```

## If tests fail

- Be specific in `notes` — include the actual vs expected output.
- The server will re-create this task for a retry (up to 3 attempts).
- On retry, the build report and rejection notes will tell you what changed.

## If all tests pass

`submit_test_results` will automatically spawn the step-9 (documenter) task.
