# M1 — registration spike

The gate before any infrastructure: take 200 frames from a real Veo export
and try to fit homographies. If per-frame registration on this footage does
not work, nothing downstream matters.

```
pip install -e ".[dev]"
sideline spike path/to/veo-export.mp4 --frames 200 --out spike/out
```

The 200 frames are taken as ten bursts of twenty consecutive samples at
5 fps, spread over the file, so neighbouring frames are 200 ms apart the
way they will be in the pipeline (`--burst 1` spreads them flat instead).
For a youth pitch pass `--pitch-length` and `--pitch-width`. The camera is
assumed to stand on the touchline at `y = -width/2`; if the far goal-line
ends up on the wrong side in the overlays, pass `--camera-side 1`.

What comes out:

- `spike/out/report.json` and the summary printed at the end: registered
  fraction (the spec's gate is 70%), confidence histogram, why frames failed,
  seconds per frame, and how far the frame centre jumps between consecutive
  registered frames. A follow-cam pans smoothly, so a jump of tens of metres
  per second is a frame that registered to the wrong place.
- `spike/out/overlays/*.jpg`: each frame with the lines it found (orange) and
  the pitch model projected through the fit (red). This is the thing to look
  at. Red on white is a fit; red on grass is not.
- `spike/out/birdseye/*.png`: the pitch from above with the frame's grass
  footprint drawn on it, for the same frames.

What it cannot tell you is the reprojection error, because real footage has
no ground truth. Two ways to get the "< 2 m at pitch centre" number: click
four landmarks on a handful of registered frames and compare (the static
registrar does exactly that fit, so `StaticRegistrar` on the clicks gives the
reference mapping), or trust the overlays. `sideline spike --selftest`
renders frames through a known camera and reports the error against truth;
that checks the machinery, not the footage.

## What the machinery does today, and what it does not

Lines only. A frame needs four painted lines, two in each direction on the
pitch, to be registered: a box end, a corner, most of the attacking third.
Midfield frames that show two touchlines, the halfway line and the centre
circle have only one line across the pitch and come back as unregistered
with that reason. If the report shows that reason dominating, the next
step is the centre circle as a first-class landmark (its intersections
with the halfway line and the pole of the vanishing line give the missing
correspondences), and after that interpolating between registered frames
across short gaps.

On synthetic frames the search picks a wrong but plausible naming of the
lines in a few midfield views where the halfway line and a penalty-box front
are 36 m apart either way; those score 0.86–0.90 against 0.91+ for right
answers, which is what the default confidence floor of 0.8 is set against.
Real footage will move that number, and the histogram is there to set it.
