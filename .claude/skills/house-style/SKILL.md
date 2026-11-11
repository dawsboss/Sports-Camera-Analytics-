---
name: house-style
description: How this repo writes commit messages and PR descriptions. Load before writing either, instead of reading git log for examples.
user-invocable: false
---

# Commits and PRs in this repo

The history reads like a lab notebook. Each entry says what was wrong or
unknown, what was measured, what changed, and what that means for the
next step. Give numbers, not adjectives.

## Commit messages

- **Summary line**: a plain sentence in the imperative. Use sentence case,
  no trailing full stop and no `feat:`/`fix:` prefix. Aim for under 60
  characters and never go past 72. Name the outcome, not the files:
  "Draw suggested pitch rings only where the placed points decide them",
  "Record the first trained models, and what a second match revealed".
- **Body**: after a blank line, wrapped at 72 columns. It explains *why*:
  what was wrong or missing, how that was found (a measurement, a failed
  run, a user report), what changed, and the evidence that it now holds,
  with the numbers ("1 of 60 frames", "29 of 32 tags", "83% of rings off
  by more than 3%, now 1-5%"). Leave out mechanics the diff already shows.
- Even a one-line change (a rewrap, a typo) gets a body of one or two
  sentences saying why.
- End with the attribution trailer(s) your instructions give, after a
  blank line, e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Merge commits keep git's default subject ("Merge branch 'main' of ...
  into training-rig", "Merge origin/main into <branch>"). A body saying
  what came in is optional.

## CHANGELOG.md

Retired on 31 October 2026 at 0.1.14, at the owner's request: git is the
history. Do not add entries. The commit body carries what an entry used to
(what was wrong or unknown, what was measured, what changed, the numbers).

## PR descriptions

- Title: the main commit's summary line, or a one-line summary of the
  branch.
- Body:
  - `## What`: 2-5 bullets.
  - `## Why`: a short paragraph built on the measurement or problem that
    motivated the change.
  - `## Checked`: the `pytest` result with count and time, any eval
    numbers, and which match was held out.
  - `## Open`: only if something was deferred.
  - End with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## Prose

- Write plain prose, not marketing.
- Explain reasoning, not mechanics.
- Put file and symbol names in backticks.
- Use British spelling, as the repo does (colour, metre, labelled,
  normalised).
