# Keypoints instead of lines: right idea, wrong weights

Prompted by [roboflow/sports](https://github.com/roboflow/sports), which
takes a different route to registration than this repository does, and a
better one.

## What they do, and why it is better

This repository's registrar finds line *segments* and then has to work
out which pitch line each one is. That naming search is most of the code
and all of the failures: real footage always contains something thin and
bright that is not a pitch line — an advertising board, a road, a goal
net, the Veo watermark — and nothing in a geometric search can know that.

Roboflow's route: a model trained to emit 32 **named** pitch points
("left penalty box, top corner"), and then `cv2.findHomography` from
those to their known coordinates. The correspondence is *given*, not
searched. There is no naming problem, so there is nothing for a road to
confuse.

That is the right architecture and this repository should adopt it. It
deletes `_search`, the plausibility checks and the scoring, and replaces
them with a keypoint model and four lines of OpenCV.

## Do their weights work on our footage? No, and loudly so

Two community 32-keypoint models trained on that layout were run on both
Veo exports (`pitch_keypoints.py`):

| Model | Worn olive field | Green school field |
| --- | --- | --- |
| `martinjolif/yolo-football-pitch-detection` | 11-17 keypoints per frame, **all in the sky** | not run past that |
| `Sabkat/football-pitch-detection` @ 960 px | 12-17 keypoints, on the field but the halfway line lands ~100 px left of the real one | 4-9 keypoints, penalty box and corner placed in open grass with the real goal clearly elsewhere |

**They are confidently wrong, which is worse than failing.** Every one of
those fits reports a 2-6 px reprojection error, because the error is
measured against the model's own points: it says they agree with one
homography, not that they are in the right place. The overlay is the only
honest check, and the overlays are wrong.

Input size matters enormously and unpredictably: the same model at 960 px
puts keypoints on the field and at 1920 px puts them in the sky. That is
the signature of a model far outside its training distribution.

## What this changes

1. **Adopt the layout, not the weights.** Use Roboflow's exact 32-vertex
   configuration (`pitch_keypoints.py` carries it verbatim) so their
   public dataset can pretrain a model, and any community model stays
   compatible. Their vertices are full-size in centimetres from a corner;
   `pitch_xy()` rescales to a real youth pitch and re-origins at the
   centre spot, which works because the points are *named*.
2. **Fine-tune on our frames.** Same conclusion the ball reached, from a
   different direction: borrow the architecture and the pretraining, own
   the data.
3. **The tagger already does most of this.** Tapping a named point is the
   interaction it was built for. Tagging pitch keypoints is the same
   gesture as tagging the ball, against a list of point names instead of
   one. That is a small extension, not a new tool, and it is what
   unblocks registration.
4. **Their datasets no longer need a Roboflow account.** The Universe
   links still return 401/403 unauthenticated, but CC-BY-4.0 mirrors of
   both the pitch and ball projects sit on Hugging Face and download
   anonymously; `spike/labels/fetch_public.py` takes them. Their keypoint
   order was checked against `VERTICES` here and matches at all 32, so
   pretraining on their images is a drop-in and far fewer of ours need
   tagging. `docs/TRAINING.md` has the recipe.

## Also worth taking from that project

- Their team classification uses image embeddings clustered into two
  kits, rather than hand-tuned colour thresholds. Relevant to S4, and
  more robust than the constrained colour clustering in the spec.
- Their ball handling confirms what was measured here independently:
  small, fast, and the hard part.
