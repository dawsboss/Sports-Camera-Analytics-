---
name: guard
description: Check the branch's changes against the project's invariants (CLAUDE.md, docs/SPEC.md) through the invariant-guard agent. Use before shipping anything structural.
argument-hint: "[base-ref, range or paths]"
context: fork
agent: invariant-guard
---

Review the changes against the project's invariants.

Scope: $ARGUMENTS

If the scope is empty, review everything on this branch that is not on
its base, plus uncommitted work.
