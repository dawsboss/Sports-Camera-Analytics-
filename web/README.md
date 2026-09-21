# Sideline Tagger

A page for tapping things on match footage, from a phone, to build the
training data the models need. `label.html` is the whole thing: one static
file, no build step, no upload, no video software. It tags two things:
the **ball**, and the **pitch**.

It works because Veo serves the follow-cam cut from its CDN with no login
and no CORS headers. A browser can *play* that video but cannot read its
pixels, which is exactly the split this needs: the page shows you the
frame and records where you tapped, and the pipeline pulls the matching
frame out of its own copy later.

## Ball mode

Pick a match, tap the ball, press **Save & next**. It works in bursts:
twenty samples a fifth of a second apart, then it jumps somewhere else in
the match. Consecutive frames are what a detector learns motion from, and
labelling them in a run also shows how far the ball moves between samples.

**Tag the misses too.** When the ball is off screen, behind a player or in
the air against the trees, press **Not visible**. Those frames are what
teach the detector to stay quiet, and without them it fires on corner
flags and white socks.

## Pitch mode

Tap a point on the little pitch at the bottom, then tap that same point on
the video. After four points the page fits a homography and draws every
remaining vertex as a dashed ring where it must be — tap one to accept it.
That is the difference between thirty taps a frame and about ten.

Four points is the minimum to save and eight or more is better. Skip any
frame where you cannot find four. Frames here are spread across the whole
match rather than taken in bursts, because what a keypoint model needs is
variety of camera angle, not continuity.

The 32 vertices and their order are Roboflow's, adopted verbatim so their
public dataset can pretrain the model. See
`spike/evals/PITCH_KEYPOINTS.md` for why keypoints replaced line-finding.

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

## Hosting it (about five taps, all doable on a phone)

The page has to be served from somewhere before it can play the footage.
A Claude artifact cannot: its content policy allows scripts and fonts
from a short list of sites and blocks all other media, so the video never
loads there however right the link is. GitHub Pages has no such rule.

This repository is public, so Pages is free. The workflow that publishes
`web/` is already committed and waiting.

1. On github.com open **dawsboss/Sports-Camera-Analytics-**
2. **Settings** (you may need the `...` menu on a narrow screen)
3. **Pages** in the left list
4. Under **Build and deployment**, set **Source** to **GitHub Actions**
5. That is all it needs. The next push to `web/` deploys; to publish
   immediately instead, open **Actions → Pages → Run workflow**.

The page then lives at:

```
https://dawsboss.github.io/Sports-Camera-Analytics-/label.html
```

Add it to the home screen and it behaves like an app. Tags are kept per
device and per site, so tags made at that address stay at that address.

## Why not just use it as a Claude artifact

That was the first attempt and it does not work, which is worth
recording so nobody tries again. An artifact is served inside a content
policy that admits scripts from a couple of CDNs and fonts from Google,
and blocks every other outside resource, media included. The page loads
and looks right; the video silently never arrives. The page now says so
when it detects it is embedded, rather than leaving a black frame.

Served from GitHub Pages, or opened as a plain local file, the same file
plays the footage. Pages is also where it can eventually write to
Firebase, which the artifact policy blocks for the same reason.

Keep that Firebase project **separate** from the minutes app's:
soccer-manager's rules are one document covering its whole database, and
its own README warns that a bad paste fails silently with every write
refused. Tagging data has no business sharing that blast radius, and the
camera project's boundary is that only finished aggregates go into the
minutes app's Firebase.
