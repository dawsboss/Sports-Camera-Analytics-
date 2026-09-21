# Real-footage evaluation: SoccerNet camera calibration

This is not the M1 gate — the gate needs a real Veo export, which nobody
has run yet. This is the next best thing available without one: real
broadcast frames with hand-annotated pitch lines, used here purely as a
way to test the registrar's code against real pixels instead of rendered
ones. No training happens; `FollowCamRegistrar` is classical computer
vision, and this script just runs it and measures the error.

## Getting the data

```
pip install SoccerNet
python -c "
from SoccerNet.Downloader import SoccerNetDownloader as SNdl
SNdl(LocalDirectory='./soccernet').downloadDataTask(task='calibration-2023', split=['test'])
"
unzip soccernet/calibration-2023/test.zip -d soccernet/calibration-2023/test_unzipped
python spike/evals/soccernet.py soccernet/calibration-2023/test_unzipped/test --n 100
```

No password or access request was needed for this split, despite what
SoccerNet's docs imply about an NDA (that applies to the raw match videos,
not these extracted, annotated frames). ~500 MB, 3,141 test images. Do not
commit the dataset itself; it is not ours to redistribute.

## What it actually measures

Each frame's JSON gives named points on real pitch lines ("Side line top",
"Big rect. left main", ...), normalized to the image. The script runs the
registrar, projects each point through the estimated mapping into pitch
metres, and measures its distance to the true line. Circles are skipped —
the script only compares straight lines, unlike the internal `_recall`
scorer the registrar itself uses, which handles both.

## First run, 100 random test frames, unmodified registrar

```
registered:    8/100 (8%)     gate: >= 70%
mean error (of the 8 registered): 52.5 m    gate: < 2 m
```

This fails the gate badly on this data. It is not simply "the confidence
floor is wrong" — sweeping it from 0.5 to 0.8 only trades registered count
against wrong-but-confident count; the *correct* answers stay pinned at
about 8 out of 100 regardless of where the floor sits. The floor is not
the bottleneck; finding the right lines is.

Two concrete causes, found by looking at the failures:

1. **Advertising boards around the pitch perimeter get detected as
   lines.** They are bright, elongated, high-contrast — exactly what
   `line_pixel_mask()` looks for — and some are green-branded (betting
   and beer sponsors), which lets them leak past the grass-colour gate
   too. Nothing in `synthetic.py`'s rendered frames has this, so the
   synthetic test suite never could have caught it.
2. **Extreme angles are out of scope.** A behind-the-goal shot through
   the netting has no resemblance to the sideline geometry the search
   and its plausibility checks assume.

When the registrar does find genuine pitch lines cleanly, it is accurate:
the frames that register land within a metre, same as the synthetic
tests. The problem so far is line-finding on real pixels, not the
homography search or the scoring built on top of it.

This dataset is a reasonable proxy for the *line-finding* problem but not
a perfect stand-in for Veo: broadcast frames here are wide, stable shots
from an elevated camera and often carry pitch-side ad boards, where a
youth-match Veo export pans and zooms tighter and usually has no boards
at all. The board problem may matter less on real Veo footage than it
does here — or a different confuser may take its place. Only a real
export answers that.

## Next lever

Reject line candidates that do not have grass on both sides along their
length, not just line pixels sitting inside the grass mask as a whole.
A pitch line has mown grass immediately on either side of it; a board
edge has grass on one side and stands, crowd, or another board on the
other. This is a small, targeted change and worth trying before anything
larger, because it matches the actual failure mode rather than a guess
at one.
