---
name: sync
description: Pull the current branch from GitHub and merge in its base (main, or whatever it was built off), stopping on conflicts, through the git-shepherd agent. Commits and pushes nothing. Use at the start of a session or when the user asks to pull or update.
argument-hint: "[base-branch]"
---

Spawn the **git-shepherd** agent with `mode: sync`. If `$ARGUMENTS`
names a branch, add `base: $ARGUMENTS`. Wait for its report.

Act on its `STATUS`:
- **`SYNCED` or `NOTHING_TO_DO`:** one line to the user: how many
  commits came in from the branch and from the base.
- **`CONFLICT`:** the agent aborted the merge, so the tree is as it was.
  Show the files and what each side did. Propose a resolution from what
  this session knows, and ask the user before redoing the merge and
  resolving it here.
- **`OFFER:`** (the branch is already merged): ask with
  AskUserQuestion. On yes, resume the agent with
  `mode: cleanup <branch>` and
  `user confirmed deleting <local|remote|both> <branch>`.
