---
name: eval
description: Run a spike/evals or spike/labels measurement on the GPU in the background, or check on a training run, through the eval-runner agent. Numbers come back the way this project quotes them.
argument-hint: "<what to measure, e.g. 'ball_on_tags, ball_ft, val split' or 'how is pitch_ft training'>"
context: fork
agent: eval-runner
disable-model-invocation: true
---

Run and report: $ARGUMENTS
