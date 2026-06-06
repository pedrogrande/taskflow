---
name: review-tests
description: Guide for the test_reviewer agent when reviewing test specs in step 6. Auto-loaded when a step-6 task is in progress.
user-invocable: false
---

# Reviewing test specs (Step 6)

You are the last gate before implementation begins. Your job is to ensure test specs are complete and testable — not to judge the implementation.

## Review checklist

For each DoD criterion:

- [ ] Is there at least one test spec that covers it?
- [ ] Does the spec's `expected_result` directly verify the criterion?

For each test spec:

- [ ] Is `description` specific enough to understand what is being tested without reading the code?
- [ ] Is `expected_result` measurable? (HTTP status + body shape, return value, file content, etc.)
- [ ] Could an automated test runner verify this without human judgement?
- [ ] Is the happy path covered?
- [ ] Is at least one error/edge case covered per criterion?

## File context (optional)

You have read-only access to the codebase. If relevant existing code is present, you may check whether specs align with the actual interface — but do not reject specs solely because the code doesn't exist yet (it hasn't been written).

## Approval

Approve when every DoD criterion has coverage and all specs are specific and verifiable.

## Rejection

Reject when:

- One or more DoD criteria have zero test coverage
- `expected_result` fields are vague ("no errors", "it works", "200")
- Specs are missing `expected_result` entirely
- Specs test the same behaviour redundantly without covering other criteria

**Your `notes` on rejection must name the specific criterion or spec that failed the check.** Do not give generic feedback.

Example rejection notes:
> "DoD criterion 'Unauthenticated requests return 401' has no corresponding test spec. Add a spec for this before approving."
