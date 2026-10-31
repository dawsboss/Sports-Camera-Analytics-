"""Score the pipeline's restarts against the minutes app's taps.

The coach taps corners, throw-ins, goal kicks, fouls and keeper claims on
the Track tab as they happen, with the player attributed afterwards. Those
taps are the only ground truth for restart detection, so this is how the
detector is measured, per kind, and how its confidences get set.

A tap trails the event: the clock is read in whole seconds at the moment
of the tap, and the coach's reaction comes first. So each tap opens a
window on the video, from `--before` seconds ahead of it to `--after`
seconds behind it, and a detected restart of the same kind inside the
window is a find. A detected restart of another kind inside the window is
a dead ball found and misnamed, which is reported separately because it
is a different failure: the stationary-ball test worked and the naming
did not. A foul's window reaches further behind, because what the camera
sees is the free kick that follows, not the foul. A goal's does too, for
the kick-off.

    python spike/evals/restarts_on_taps.py --events <events.parquet> \\
        --sm <matches-node.json> --kickoff-offset-ms 83000 [--before 15 --after 3]

or, with a local blob store, `--match-dir data/blobs/matches/<id>`, which
takes the latest events version and the kickoff offset from match.json.
Detected restarts of kinds the coach did not track (`--kinds`) are listed,
not scored: a free kick nobody tapped is not a false alarm.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Iterable, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sideline.sports.soccer import RESTART_TYPES  # noqa: E402
from sideline.timeline import Tap, periods_from_sm, taps_from_sm, video_ms_for_elapsed  # noqa: E402

# A tap's kind in the minutes app, and the restart the camera should see for it.
KIND_TO_TYPE = {"corner": "corner", "throw": "throw_in", "goalkick": "goal_kick", "keeper": "keeper_claim",
                "foul": "free_kick", "goal": "kick_off"}
# For these the restart follows the tap rather than preceding it.
EXTRA_AFTER_S = {"foul": 30.0, "goal": 90.0}


@dataclass
class KindScore:
    taps: int = 0
    found: int = 0
    misnamed: Counter = field(default_factory=Counter)
    missed: int = 0
    offsets_s: list[float] = field(default_factory=list)      # tap time minus detected time, for finds
    attributed: int = 0                                        # taps with a player, among finds

    def as_dict(self) -> dict:
        return {"taps": self.taps, "found": self.found, "misnamed": dict(self.misnamed), "missed": self.missed,
                "median_offset_s": round(median(self.offsets_s), 1) if self.offsets_s else None,
                "attributed_finds": self.attributed}


@dataclass
class Report:
    kinds: dict[str, KindScore]
    false_alarms: Counter            # detected restarts of scored types matched to no tap
    unscored: Counter                # detected restarts of types nobody tapped for
    taps_off_video: int              # taps that map to no video time (clock past the file, no periods)

    def as_dict(self) -> dict:
        return {"kinds": {k: v.as_dict() for k, v in self.kinds.items()}, "false_alarms": dict(self.false_alarms),
                "unscored": dict(self.unscored), "taps_off_video": self.taps_off_video}


def score(detected: list[dict], taps: list[tuple[Tap, Optional[int]]], before_s: float, after_s: float,
          kinds: Iterable[str]) -> Report:
    """`detected` rows carry t_ms and type (events.parquet); `taps` pair each
    tap with its video time or None. Each detection answers at most one tap."""
    kinds = [k for k in kinds if k in KIND_TO_TYPE]
    scored_types = {KIND_TO_TYPE[k] for k in kinds}
    dets = sorted((d for d in detected if d["type"] in RESTART_TYPES), key=lambda d: d["t_ms"])
    used = [False] * len(dets)
    report = Report({k: KindScore() for k in kinds}, Counter(), Counter(), 0)
    for tap, v in sorted(taps, key=lambda tv: tv[0].elapsed_s):
        if tap.kind not in report.kinds:
            continue
        ks = report.kinds[tap.kind]
        ks.taps += 1
        if v is None:
            report.taps_off_video += 1
            ks.missed += 1
            continue
        lo = v - before_s * 1000
        hi = v + (after_s + EXTRA_AFTER_S.get(tap.kind, 0.0)) * 1000
        want = KIND_TO_TYPE[tap.kind]
        inside = [i for i, d in enumerate(dets) if not used[i] and lo <= d["t_ms"] <= hi]
        same = [i for i in inside if dets[i]["type"] == want]
        if same:
            i = min(same, key=lambda i: abs(dets[i]["t_ms"] - v))
            used[i] = True
            ks.found += 1
            ks.offsets_s.append((v - dets[i]["t_ms"]) / 1000)
            if tap.player_id:
                ks.attributed += 1
        elif inside:
            i = min(inside, key=lambda i: abs(dets[i]["t_ms"] - v))
            used[i] = True
            ks.misnamed[dets[i]["type"]] += 1
        else:
            ks.missed += 1
    for i, d in enumerate(dets):
        if used[i]:
            continue
        (report.false_alarms if d["type"] in scored_types else report.unscored)[d["type"]] += 1
    return report


def format_report(r: Report) -> str:
    lines = [f"{'kind':<10}{'taps':>6}{'found':>7}{'misnamed':>10}{'missed':>8}{'offset s':>10}{'w/ player':>11}"]
    for k, s in r.kinds.items():
        off = f"{median(s.offsets_s):.1f}" if s.offsets_s else "-"
        lines.append(f"{k:<10}{s.taps:>6}{s.found:>7}{sum(s.misnamed.values()):>10}{s.missed:>8}{off:>10}{s.attributed:>11}")
        for t, n in s.misnamed.most_common():
            lines.append(f"{'':<10}  found as {t}: {n}")
    fa = ", ".join(f"{t} {n}" for t, n in r.false_alarms.most_common()) or "none"
    un = ", ".join(f"{t} {n}" for t, n in r.unscored.most_common()) or "none"
    lines.append(f"false alarms (scored kinds, no tap): {fa}")
    lines.append(f"detected but not tracked by the coach: {un}")
    if r.taps_off_video:
        lines.append(f"taps with no video time: {r.taps_off_video}")
    return "\n".join(lines)


def load_events(path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    return pq.read_table(path).to_pylist()


def latest_events(match_dir: Path) -> tuple[Path, Optional[int]]:
    versions = sorted((match_dir / "s8_events").glob("v*/events.parquet"))
    if not versions:
        raise SystemExit(f"no s8_events version under {match_dir}")
    s0 = sorted((match_dir / "s0_ingest").glob("v*/match.json"))
    kickoff = json.loads(s0[-1].read_text()).get("kickoff_offset_ms") if s0 else None
    return versions[-1], kickoff


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", type=Path, help="events.parquet from s8_events")
    ap.add_argument("--match-dir", type=Path, help="matches/<id> in a local blob store; takes the latest s8_events")
    ap.add_argument("--sm", type=Path, required=True, help="the soccer-manager matches/{id} node as JSON")
    ap.add_argument("--kickoff-offset-ms", type=int, default=None, help="video time of the first whistle")
    ap.add_argument("--before", type=float, default=15.0, help="seconds a restart may precede its tap")
    ap.add_argument("--after", type=float, default=3.0, help="seconds a restart may follow its tap")
    ap.add_argument("--kinds", default="corner,throw,goalkick,keeper,foul,goal",
                    help="the kinds the coach tracked; others are not scored")
    ap.add_argument("--out", type=Path, default=None, help="write the report as JSON here too")
    args = ap.parse_args(argv)

    kickoff = args.kickoff_offset_ms
    if args.events is None and args.match_dir is None:
        ap.error("give --events or --match-dir")
    events_path = args.events
    if args.match_dir is not None:
        events_path, k0 = latest_events(args.match_dir)
        kickoff = kickoff if kickoff is not None else k0
    if kickoff is None:
        ap.error("--kickoff-offset-ms is needed (or a match.json that carries it)")
    sm = json.loads(args.sm.read_text())
    periods = periods_from_sm(sm.get("periods") or {})
    taps = taps_from_sm(sm)
    paired = [(t, video_ms_for_elapsed(t.elapsed_s, kickoff, periods)) for t in taps]
    report = score(load_events(events_path), paired, args.before, args.after, args.kinds.split(","))
    print(format_report(report))
    if args.out:
        args.out.write_text(json.dumps(report.as_dict(), indent=2))
        print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
