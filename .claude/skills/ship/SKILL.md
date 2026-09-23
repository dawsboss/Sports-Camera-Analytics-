---
name: ship
description: Commit the current work, sync with the branch's remote and its base, run the tests and push, all through the git-shepherd agent, which writes the message and CHANGELOG entry in house style. Use when the user asks to commit, push or ship.
argument-hint: "[base-branch] [notes on why]"
---

Hand this to the **git-shepherd** agent. Do not run the git commands
here.

1. **Write the agent's prompt.** Include what this conversation knows
   and the diff does not:
   - `mode: ship`
   - `why:` 2-6 sentences: what was wrong, missing or measured, what
     changed, and the evidence, with numbers. This becomes the commit
     body and the CHANGELOG entry. Fold in any notes from: $ARGUMENTS
   - `files:` the paths this change touched, if other uncommitted work
     is lying around.
   - `base:` if the arguments name one, or the branch was cut from
     something other than `main`.
   - `trailer:` the exact attribution line(s) your instructions give
     for commits.
   - `tests passed at <sha>`: only if pytest passed on this exact tree
     in this session and nothing has changed since.
2. **Run it.** Spawn the agent, tell the user it is running, and wait
   for its report. Do not poll.
3. **Act on its `STATUS`:**
   - `PUSHED`: one line to the user with the branch, the commit summary
     and the test result.
   - `CONFLICT`: nothing was pushed. Show the conflicting files and what
     each side did, briefly. For each file, propose a resolution based
     on what this session knows, and **ask the user before resolving**.
     Once they agree, redo the merge here
     (`git merge --no-edit origin/<branch-or-base>`), resolve it, commit
     the merge, and run /ship again.
   - `TESTS_FAILED`: nothing was pushed. Show the failures. Fixing them
     is work for this session, not the agent.
   - `NEEDS_INPUT`: answer from context if you can and resume the agent
     with SendMessage. Otherwise ask the user.
   - `OFFER:` lines: put each to the user with AskUserQuestion. On yes,
     resume the agent (SendMessage to it, or spawn git-shepherd again)
     with `mode: cleanup <branch>` and
     `user confirmed deleting <local|remote|both> <branch>`.
