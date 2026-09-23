---
name: house-style
description: How this repo writes commit messages, CHANGELOG.md entries and PR descriptions. Load before writing any of them, instead of reading CHANGELOG.md or git log for examples.
user-invocable: false
---

# Commits, CHANGELOG and PRs in this repo

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

- **Oldest first.** New entries go at the bottom of the file.
- Heading: `## 0.1.N — <lowercase phrase: what changed or was learned>`,
  with an em dash. Example: `## 0.1.8 — the first training run, on a
  gaming PC, and what it found first`.
- One entry per branch or PR. If the last entry's heading is not on
  `origin/<base>` yet, it belongs to this branch's work: add to it when
  this change is part of that work, otherwise start the next patch
  version. If the last entry is already on the base, start the next
  patch version.
- Shape: an opening paragraph with what happened and the headline
  finding, then bullets. Each bullet starts with a **bold claim** or a
  **`file name`** and continues in prose: what was wrong, how it was
  measured, what happens now, and the number. Point to the file that
  holds the measurements (for example `spike/evals/training_2026-09-23.md`).
- State what did *not* work as plainly as what did. A negative
  measurement counts as a result here.
- Wrap at the width the file already uses (about 74 columns).
- Every shipped change gets an entry, except pure merges and rewraps or
  typo fixes.

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
