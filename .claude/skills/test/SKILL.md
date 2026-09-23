---
name: test
description: Run pytest in the background through the test-runner agent and get a compact verdict, including whether each failure is new or was already broken on the base. Use after any change instead of running the suite in the main session.
argument-hint: "[pytest args, e.g. tests/test_lines.py or -k sweep]"
context: fork
agent: test-runner
---

Run this repo's tests with these pytest arguments (empty means the
whole suite): $ARGUMENTS
