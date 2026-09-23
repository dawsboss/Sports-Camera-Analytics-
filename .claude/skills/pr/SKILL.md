---
name: pr
description: Ship the branch (commit, sync with its remote and base, test, push) and open a pull request, then offer to delete the branch, all through the git-shepherd agent. Use when the user asks for a PR.
argument-hint: "[base-branch] [notes for the PR]"
---

Hand this to the **git-shepherd** agent. Do not run the git or gh
commands here.

1. **Write the agent's prompt:**
   - `mode: pr`
   - `why:` 2-6 sentences on what this branch does and why, with the
     numbers that motivated it. This becomes the PR's "Why" section,
     and the commit body if anything is still uncommitted. Fold in any
     notes from: $ARGUMENTS
   - `checked:` what was verified in this session: the pytest result
     and any eval numbers, with the match and split.
   - `base:` if the arguments name one, or the branch was cut from
     something other than `main`.
   - `trailer:` the exact attribution line(s) your instructions give
     for commits.
2. **Run it.** Spawn the agent, tell the user it is running, and wait.
3. **Act on its `STATUS`:**
   - `PR_OPENED`: give the PR URL. If `gh` is not installed, the agent
     returns a compare link and a body file instead. Give the user the
     link and say the body is ready to paste from that file.
   - `CONFLICT`, `TESTS_FAILED`, `NEEDS_INPUT`: handle them as /ship
     does. Nothing was pushed; propose, and ask before resolving
     conflicts.
   - **The `OFFER:`** always comes after a PR. Ask with
     AskUserQuestion. While the PR is open, only the **local** branch
     is offered, because deleting the remote branch would close the PR.
     On yes, resume the agent (or spawn git-shepherd again) with
     `mode: cleanup <branch>` and
     `user confirmed deleting local <branch>`. Once the PR has merged,
     /sync or /pr on that branch will offer the remote branch too.
