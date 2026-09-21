# Ball Tagger

A page for tapping the ball on match footage, from a phone, to build the
training data a ball detector needs. `label.html` is the whole thing: one
static file, no build step, no upload, no video software.

It works because Veo serves the follow-cam cut from its CDN with no login
and no CORS headers. A browser can *play* that video but cannot read its
pixels, which is exactly the split this needs: the page shows you the
frame and records where you tapped, and the pipeline pulls the matching
frame out of its own copy later.

## Using it

Open the page, pick a match, tap the ball, press **Save & next**. It works
in bursts: twenty samples a fifth of a second apart, then it jumps
somewhere else in the match. Consecutive frames are what a detector learns
motion from, and labelling them in a run also shows how far the ball moves
between samples.

**Tag the misses too.** When the ball is off screen, behind a player or in
the air against the trees, press **Not visible**. Those frames are what
teach the detector to stay quiet, and without them it fires on corner
flags and white socks.

Tags are kept in the browser on that device. When you have a few hundred,
open **Menu** and **Copy tags**, and paste the JSON somewhere the pipeline
can read it.

## Turning tags into a training set

```
python spike/labels/build_dataset.py --tags tags.json \
    --video 20260919-flight=/path/to/flight.mp4 \
    --video 20260920-future=/path/to/future.mp4 \
    --out data/ball --holdout 20260920-future
```

That cuts the tagged frames out of the videos and writes them in the
layout Ultralytics trains from, with the held-out match kept in `val/`
so its score means something. The script prints the training command.

## Adding a match

The page needs the direct video file, not the Veo match page, and it
cannot work the one out from the other in a browser (Veo's page blocks
cross-origin reads). Send the match link to Claude and it will resolve it
and add it to the list in `label.html`.

## Where it runs

Two places, same file:

- **As a Claude artifact**, which is how it got used first — instant on a
  phone, but the sandbox blocks file downloads and cross-origin requests,
  so it is local storage and copy-out only.
- **On GitHub Pages**, alongside the minutes app, which is where it
  belongs long term. There it can also write to Firebase, if a project is
  set up for it. Keep that a *separate* Firebase project from the minutes
  app: soccer-manager's rules are one document covering its whole
  database, and its own README warns that a bad paste fails silently with
  every write refused. Tagging data has no business sharing that blast
  radius, and the camera project's boundary is that only finished
  aggregates go into the minutes app's Firebase.
