"""The `sideline` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _spike(args: argparse.Namespace) -> int:
    from sideline.registration.followcam import FollowCamConfig
    from sideline.spike import format_report, run_spike

    if not args.selftest and args.video is None:
        print("give a video file, or --selftest", file=sys.stderr)
        return 2
    cfg = FollowCamConfig(min_confidence=args.min_confidence, camera_side=args.camera_side)
    report = run_spike(
        Path(args.video) if args.video else None, Path(args.out), frames=args.frames,
        pitch=(args.pitch_length, args.pitch_width), config=cfg, selftest=args.selftest,
        overlays=not args.no_overlays, burst=args.burst,
    )
    print(format_report(report))
    print(f"report: {Path(args.out) / 'report.json'}")
    return 0


def _ingest(args: argparse.Namespace) -> int:
    from sideline.config import Settings
    from sideline.pipeline import ingest_local

    settings = Settings.from_env(data_dir=Path(args.data))
    config: dict = {"grass_filter": not args.no_grass_filter}
    if args.max_frames is not None:
        config["max_frames"] = args.max_frames
    if args.weights:
        # One checkpoint per --weights, all run on every frame; a COCO model
        # for the people and a fine-tuned one for the ball is the expected pair.
        config["detectors"] = [{"weights": w, "imgsz": args.imgsz} for w in args.weights]
    match_id = ingest_local(
        settings, Path(args.video), capture_mode=args.mode, sm_match_id=args.sm_match_id,
        pitch=(args.pitch_length, args.pitch_width), kickoff_offset_ms=args.kickoff_offset_ms,
        stages=args.stages.split(","), config=config,
    )
    print(match_id)
    return 0


def _run(args: argparse.Namespace) -> int:
    """Run stages on a match that exists already: the GPU machine's half of
    a match the homelab ingested."""
    from sideline.config import Settings
    from sideline.pipeline import run_stages

    settings = Settings.from_env(data_dir=Path(args.data))
    config: dict = {"grass_filter": not args.no_grass_filter}
    if args.max_frames is not None:
        config["max_frames"] = args.max_frames
    if args.weights:
        config["detectors"] = [{"weights": w, "imgsz": args.imgsz} for w in args.weights]
    for ref in run_stages(settings, args.match_id, args.stages.split(","), config):
        print(f"{ref.stage} v{ref.version}")
    return 0


def _log(args: argparse.Namespace) -> int:
    """Print the match's events as a coach would write them down."""
    from sideline.config import Settings, build_blobs
    from sideline.storage.artifacts import ArtifactStore

    settings = Settings.from_env(data_dir=Path(args.data))
    store = ArtifactStore(build_blobs(settings))
    ref = store.latest(args.match_id, "s8_events")
    if ref is None or not store.complete(ref):
        print(f"no events for match {args.match_id}; run s8_events first", file=sys.stderr)
        return 1
    text = store.blobs.get(ref.key("events.csv")).decode()
    if args.csv:
        print(text, end="")
        return 0
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(text)))
    basis = rows[0]["clock_basis"] if rows else "match"
    print(f"{'clock' if basis == 'match' else 'video':<8}{'event':<14}{'who':<16}{'sure':>5}  why")
    for r in rows:
        who = r["player_id"] or (f"track {r['taker_track']}" if r["taker_track"] else "nobody near")
        if r["team"]:
            who += f" ({r['team']})"
        print(f"{r['clock']:<8}{r['event']:<14}{who:<16}{r['confidence']:>5}  {r['reason']}")
    print(f"{len(rows)} events, s8_events v{ref.version}")
    return 0


def _sport_of(settings, match_id: str) -> str:
    """The match's own sport, from its record; soccer only if the match is
    not in this database (artifacts copied in from elsewhere)."""
    from sideline.config import build_engine
    from sideline.db.models import Match
    from sideline.db.session import session_scope

    with session_scope(build_engine(settings)) as s:
        m = s.get(Match, match_id)
        return m.sport if m is not None else "soccer"


def _overlay(args: argparse.Namespace) -> int:
    """Render what the stages saw, to watch a run decide."""
    from sideline.config import Settings, build_blobs
    from sideline.overlay import render_overlay
    from sideline.sports import get_sport
    from sideline.storage.artifacts import ArtifactStore

    settings = Settings.from_env(data_dir=Path(args.data))
    store = ArtifactStore(build_blobs(settings))
    sport = args.sport or _sport_of(settings, args.match_id)
    objects = {c.name for c in get_sport(sport).entity_classes() if c.object}
    out = Path(args.out) if args.out else Path(args.data) / f"{args.match_id}_overlay.mp4"
    info = render_overlay(store, args.match_id, out, around_s=args.around, hold_s=args.hold,
                          fps=args.fps, object_classes=objects)
    print(f"{info['frames']} frames, {info['events']} events -> {info['out']}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    # The API reads its settings from the environment when it imports, so
    # `--data` goes there: the same local directory `sideline ingest` wrote,
    # which is how a run on one machine is reviewed from a phone on the
    # same network with no services.
    if args.data:
        os.environ["SIDELINE_DATA_DIR"] = args.data
    # One machine, no Redis: uploads return at once and the stages run on a
    # background thread of this process. Redis, when configured, still wins.
    os.environ.setdefault("SIDELINE_JOB_RUNNER", "rq" if os.environ.get("SIDELINE_REDIS_URL") else "thread")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("serving the review page and sampled frames to every device on this network; "
              "keep this port off the internet (no public tunnel), or bind --host 127.0.0.1", file=sys.stderr)
    uvicorn.run("sideline.api.app:app", host=args.host, port=args.port, reload=False)
    return 0


def _worker(args: argparse.Namespace) -> int:
    from sideline.worker import main as worker_main

    return worker_main()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sideline", description="Static-camera sports analytics pipeline.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("spike", help="M1: try per-frame registration on frames from a Veo export")
    s.add_argument("video", nargs="?", help="MP4 exported from the Veo editor")
    s.add_argument("--frames", type=int, default=200)
    s.add_argument("--burst", type=int, default=20, help="consecutive 5 fps samples per burst; bursts are spread over the file")
    s.add_argument("--out", default="spike/out")
    s.add_argument("--pitch-length", type=float, default=105.0)
    s.add_argument("--pitch-width", type=float, default=68.0)
    s.add_argument("--min-confidence", type=float, default=0.8)
    s.add_argument("--camera-side", type=int, choices=(-1, 1), default=-1,
                   help="sign of the camera's y coordinate; the near touchline is y = -width/2 with the default")
    s.add_argument("--selftest", action="store_true", help="rendered frames with known truth instead of a video")
    s.add_argument("--no-overlays", action="store_true")
    s.set_defaults(fn=_spike)

    i = sub.add_parser("ingest", help="register a match locally and run stages without any services")
    i.add_argument("video")
    i.add_argument("--data", default="./data")
    i.add_argument("--mode", choices=("static", "followcam"), default="followcam")
    i.add_argument("--sm-match-id", default=None)
    i.add_argument("--pitch-length", type=float, default=105.0)
    i.add_argument("--pitch-width", type=float, default=68.0)
    i.add_argument("--kickoff-offset-ms", type=int, default=None)
    i.add_argument("--stages", default="s0_ingest,s1_sampling",
                   help="comma-separated; add s2_detection,s3_tracking,s8_events for players, the ball and restarts")
    i.add_argument("--weights", action="append", default=None,
                   help="detector checkpoint(s) for s2_detection; default is the COCO model yolo11x.pt")
    i.add_argument("--imgsz", type=int, default=1920, help="inference size for s2_detection")
    i.add_argument("--no-grass-filter", action="store_true", help="keep people whose feet are off the grass")
    i.add_argument("--max-frames", type=int, default=None,
                   help="sample and detect only this many frames, for a quick run on a slice of the match")
    i.set_defaults(fn=_ingest)

    r = sub.add_parser("run", help="run stages on an existing match, e.g. detection on a GPU machine")
    r.add_argument("match_id")
    r.add_argument("--data", default="./data")
    r.add_argument("--stages", default="s2_detection,s3_tracking,s8_events")
    r.add_argument("--weights", action="append", default=None, help="detector checkpoint(s); default yolo11x.pt")
    r.add_argument("--imgsz", type=int, default=1920)
    r.add_argument("--no-grass-filter", action="store_true")
    r.add_argument("--max-frames", type=int, default=None)
    r.set_defaults(fn=_run)

    g = sub.add_parser("log", help="print a match's events: the clock, what happened, who, how sure")
    g.add_argument("match_id")
    g.add_argument("--data", default="./data")
    g.add_argument("--csv", action="store_true", help="the raw events.csv instead of a table")
    g.set_defaults(fn=_log)

    o = sub.add_parser("overlay", help="render the sampled frames with tracks, the ball's path and each event's call")
    o.add_argument("match_id")
    o.add_argument("--data", default="./data")
    o.add_argument("--out", default=None, help="MP4 path; default data/<match>_overlay.mp4")
    o.add_argument("--around", type=float, default=None, help="only the seconds around each event, e.g. 8")
    o.add_argument("--hold", type=float, default=3.0, help="seconds an event's caption stays up")
    o.add_argument("--fps", type=float, default=None, help="playback rate; default is the sample rate")
    o.add_argument("--sport", default=None, help="default: the match's own sport from its record")
    o.set_defaults(fn=_overlay)

    v = sub.add_parser("serve", help="run the API and the review page, for this network only")
    v.add_argument("--host", default="0.0.0.0")
    v.add_argument("--port", type=int, default=8000)
    v.add_argument("--data", default=None, help="a local data directory from `sideline ingest`, instead of the services")
    v.set_defaults(fn=_serve)

    w = sub.add_parser("worker", help="run an RQ worker")
    w.set_defaults(fn=_worker)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
