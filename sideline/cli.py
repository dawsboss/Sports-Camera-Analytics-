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
    match_id = ingest_local(
        settings, Path(args.video), capture_mode=args.mode, sm_match_id=args.sm_match_id,
        pitch=(args.pitch_length, args.pitch_width), kickoff_offset_ms=args.kickoff_offset_ms,
        stages=args.stages.split(","),
    )
    print(match_id)
    return 0


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

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

    i = sub.add_parser("ingest", help="register a match locally and run S0/S1 without any services")
    i.add_argument("video")
    i.add_argument("--data", default="./data")
    i.add_argument("--mode", choices=("static", "followcam"), default="followcam")
    i.add_argument("--sm-match-id", default=None)
    i.add_argument("--pitch-length", type=float, default=105.0)
    i.add_argument("--pitch-width", type=float, default=68.0)
    i.add_argument("--kickoff-offset-ms", type=int, default=None)
    i.add_argument("--stages", default="s0_ingest,s1_sampling")
    i.set_defaults(fn=_ingest)

    v = sub.add_parser("serve", help="run the API")
    v.add_argument("--host", default="0.0.0.0")
    v.add_argument("--port", type=int, default=8000)
    v.set_defaults(fn=_serve)

    w = sub.add_parser("worker", help="run an RQ worker")
    w.set_defaults(fn=_worker)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
