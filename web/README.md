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

## Moving around the frame

The whole frame is under one set of gestures, in both modes:

| | |
|---|---|
| tap | put a mark down, or accept a suggested one |
| drag a mark | move that mark |
| drag anywhere else | move the frame |
| pinch, or **&minus;** / **+** | zoom; the number between them fits the frame again |

Nothing but a tap ever places a mark, and a tap is a press that does not
travel. That distinction is the whole point: looking closer used to count
as a tap, so pinching to check a ball moved the pin off it.

## Ball mode

Pick a match, tap the ball, press **Save & next**. The first tap zooms to
4x on the spot you hit so you can see whether you got it; drag the pin to
nudge it, and pinch or drag if the ball turns out to be somewhere else.

It works in bursts: twenty samples a fifth of a second apart, then it
jumps somewhere else in the match. Consecutive frames are what a detector learns motion from, and
labelling them in a run also shows how far the ball moves between samples.

**Tag the misses too.** When the ball is off screen, behind a player or in
the air against the trees, press **Not visible**. Those frames are what
teach the detector to stay quiet, and without them it fires on corner
flags and white socks.

## Pitch mode

Tap a point on the little pitch at the bottom, then tap that same point on
the video. After four points the page fits a homography and draws every
remaining vertex as a ring where it must be — tap inside one to accept it.
That is the difference between thirty taps a frame and about ten.

Four points is the minimum to save and eight or more is better. Skip any
frame where you cannot find four. Frames here are spread across the whole
match rather than taken in bursts, because what a keypoint model needs is
variety of camera angle, not continuity.

**A point you cannot see** — under a player, past the edge of the frame,
or paint that was never painted on a school field — press **can't see it**
and it moves to the next one. Leaving it out is the right answer, not a
compromise: the model learns which points are in a frame as much as where
they are, so a guessed corner is a wrong label while a missing one costs
nothing. A point set aside greys out on the diagram and stops being
suggested for that frame.

**Undo** takes back the point placed last, then the one before it. The
pins are held in an object keyed by vertex number, and JavaScript hands
those back in numeric order however they went in — so undo used to remove
the highest-numbered point instead, which is why it behaved like a queue.
The order of placement is now kept separately.

The 32 vertices and their order are Roboflow's, adopted verbatim so their
public dataset can pretrain the model. See
`spike/evals/PITCH_KEYPOINTS.md` for why keypoints replaced line-finding.

## Where the tags are kept

In the browser, on that device, and nowhere else. There is no database,
no account and no server: the page writes one `localStorage` key,
`sidelinetagger.v2`, holding every match's tags as JSON, and saves it
after each **Save & next**. Nothing is uploaded — the page cannot even
read the video's pixels, which is what makes it legal to point at Veo's
CDN at all.

That has one consequence worth taking seriously: **clearing the browser's
site data deletes the lot**, and tags made at one address are not visible
at another (a local file and the Pages site are different origins, as are
two different phones). So copy them out regularly rather than at the end.
Open **&ctdot;** and **Copy tags out**, and keep the JSON somewhere real.

## How this turns into a fine-tuned detector

The tags are the supervision, and nothing else in this project can supply
it. `build_dataset.py` cuts each tagged frame out of the local copy of the
video and writes the pair the trainer wants: the image, and a text file
saying what is in it — a box the measured 11 px wide around the ball, or
the 32 pitch vertices with the ones you did not place marked absent.
Ultralytics then fine-tunes from those, a COCO detector for the ball and a
pose model for the pitch, with one whole match held out so the score means
something. Frames where you pressed **Not visible** become empty label
files, which is how a detector is taught what is *not* a ball.

The counts that matter: a few hundred ball frames per field, and a couple
of hundred pitch frames spread across the match. The worn 19 Sept field is
worth more than the green one — the ball is found 43% of the time there
against 73% on grass that looks like everything these models were trained
on, and it is that gap the fine-tune exists to close.

Copied-out tags become that training set in one command:

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

## Checking the gestures

`tagger.smoke.js` drives the page in a real Chromium and asserts what the
taps do: that a tap places a mark and a pinch does not, that a drag moves
the frame unless it started on a mark, that a tap beside a placed point
still places the next one, and that undo walks back the order things were
placed in. It needs Playwright and is not part of `pytest`:

```
npm i playwright
node web/tagger.smoke.js
```

The video never loads there, which is fine: what is under test is where a
tap lands, not what it lands on.
