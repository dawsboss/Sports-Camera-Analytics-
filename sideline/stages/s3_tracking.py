"""S3 — Tracking.

Join S2's detections into short-lived fragments and write
`fragments.parquet`: one row per track per sampled frame it was seen in,
still in image pixels. No long-range re-identification happens here; a
player who leaves the frame and comes back is a new fragment, and linking
fragments into one player is identity resolution's job (S6), with the
review UI behind it. On follow-cam footage the spec expects dozens of
fragments per player per half, and that is fine: the stage's job is to
keep each fragment clean, not long.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

import pyarrow as pa

from sideline.contracts import FRAGMENTS_SCHEMA
from sideline.detection import Detection
from sideline.sports import get_sport
from sideline.stages.base import StageContext
from sideline.storage.artifacts import ArtifactRef
from sideline.tracking import TrackerConfig, track_frames


class TrackingStage:
    name = "s3_tracking"
    version = "0.1.0"

    def run(self, ctx: StageContext) -> ArtifactRef:
        m = ctx.match
        s2 = ctx.artifacts.latest(m.id, "s2_detection")
        if s2 is None or not ctx.artifacts.complete(s2):
            raise RuntimeError("s3_tracking needs a completed s2_detection version")
        s0 = ctx.artifacts.latest(m.id, "s0_ingest")
        if s0 is None:
            raise RuntimeError("s3_tracking needs s0_ingest for the frame size")
        match = ctx.artifacts.read_json(s0, "match.json")
        dets = ctx.artifacts.read_table(s2, "detections.parquet").to_pylist()

        by_frame: dict[int, list[Detection]] = defaultdict(list)
        t_of: dict[int, int] = {}
        for r in dets:
            by_frame[r["frame_index"]].append(Detection(r["cls"], r["x1"], r["y1"], r["x2"], r["y2"], r["conf"]))
            t_of[r["frame_index"]] = r["t_ms"]
        cfg_in = {k: v for k, v in ctx.config.items() if k in TrackerConfig.__dataclass_fields__}
        cfg = TrackerConfig(**cfg_in)
        object_classes = [c.name for c in get_sport(m.sport).entity_classes() if c.object]
        rows = track_frames(((k, t_of[k], by_frame[k]) for k in sorted(by_frame)), cfg, object_classes,
                            (match["width"], match["height"]))

        ref = ctx.artifacts.new_version(m.id, self.name)
        ctx.artifacts.write_table(ref, "fragments.parquet", pa.Table.from_pylist(rows, schema=FRAGMENTS_SCHEMA))
        per_track: dict[int, list[dict]] = defaultdict(list)
        for r in rows:
            per_track[r["track_id"]].append(r)
        classes = Counter(rs[0]["cls"] for rs in per_track.values())
        lengths = [len(rs) for rs in per_track.values()]
        ctx.artifacts.write_manifest(
            ref, config=cfg.__dict__, inputs=[s2, s0], capture_mode=m.capture_mode, stage_version=self.version,
            pipeline_version=ctx.pipeline_version, outputs=["fragments.parquet"],
            extra={"fragments": len(per_track), "fragments_by_class": dict(classes), "rows": len(rows),
                   "median_fragment_samples": median(lengths) if lengths else 0},
        )
        return ref
