---
name: start-project
description: Start a new TaskFlow project. Prompts for a project brief (file path or inline text), creates the project record, and seeds the pipeline for the product manager. Use this to kick off a new development cycle.
argument-hint: "[brief file path or leave blank to enter text]"
disable-model-invocation: true
---

# Start a new TaskFlow project

## Steps

1. Ask the user: "Do you have a brief file, or would you like to enter the brief as text?"

2. **If file path provided:**
   - Use your file-read tool to read the file content.
   - Note the file path for traceability.

3. **If inline text:**
   - Ask the user to paste or describe the project brief.
   - Set `brief_path` to `null`.

4. Ask the user for a short project name if not already clear from the brief.

5. Call `start_project(name=..., brief_text=..., brief_path=...)`.

6. Report back:
   - Project ID and name
   - Confirm a step-3 task has been seeded for the product manager
   - Tell the user: "Invoke the **TaskFlow Product Manager** agent to begin defining features."

## Notes

- `brief_text` must contain the full content — not just a summary. Agents without file access depend on this field.
- If the user provides a file, still copy the full content into `brief_text`.
