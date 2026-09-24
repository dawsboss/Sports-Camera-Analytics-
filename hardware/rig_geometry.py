"""What the four-camera head sees from a given mast: arithmetic, not a measurement.

Projects the pitch through the head's cameras and reports, for the worst
point on the surface, the numbers the pipeline cares about: ball size in
pixels (the detectors here learned 8-17 px balls), player height, and how
many metres one pixel of foot position covers. It also draws an "aim card"
per camera, the pitch lines where that camera should see them, to trim the
swivels against the live view, and a top-down map of which camera serves
each part of the pitch.

    python hardware/rig_geometry.py                          # 8 m mast, 10 m back, 100 x 64
    python hardware/rig_geometry.py --mast 7.4 --setback 9 --length 69 --width 46 --out aim/
    python hardware/rig_geometry.py --head reolink-833a      # the hidden pod's cameras

Lenses are modelled f-theta with the datasheet fields of view: the Milesight
MS-C8164-PD's, whose H:V ratios match 16:9 f-theta within two degrees, or
the Reolink RLC-833A's zoom at the pod's two settings.
Real lenses bend straight lines a little differently near the edges, so a
card is good for aiming to a degree or two, not for calibration: the
pitch mapping itself always comes from clicked landmarks (M10).

Frame: metres, origin at the centre spot, x along the length, y across,
z up; the camera stands on the y < 0 touchline (the pipeline's default
camera side), on the halfway line.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Datasheet fields of view (degrees, horizontal x vertical) at 3840 x 2160:
# the Milesight MS-C8164-PD's fixed lenses, and the Reolink RLC-833A's zoom
# at the two settings the pod uses. Its sheet gives 94 x 53 at the wide end
# and 50 x 30 at the long end; the vertical here is interpolated between.
LENSES = {"2.8mm": (110.0, 60.0), "4mm": (91.0, 50.0), "6mm": (55.0, 32.0),
          "833A@54": (54.0, 32.1), "833A@84": (84.0, 47.8)}
W_PX, H_PX = 3840, 2160

# Each head's aims: yaw from straight across the pitch, positive to the
# right; tilt down. "milesight" is the open printed head
# (hardware/head/sideline_head.scad), "reolink-833a" the hidden pod
# (hardware/pod/sideline_pod.scad).
HEADS = {
    "milesight": (
        ("FAR-L", -25.5, 4.0, "6mm"),
        ("FAR-R", 25.5, 4.0, "6mm"),
        ("NEAR-L", -40.0, 26.0, "4mm"),
        ("NEAR-R", 40.0, 26.0, "4mm"),
    ),
    "reolink-833a": (
        ("FAR-L", -26.0, 4.0, "833A@54"),
        ("FAR-R", 26.0, 4.0, "833A@54"),
        ("NEAR-L", -39.0, 27.0, "833A@84"),
        ("NEAR-R", 39.0, 27.0, "833A@84"),
    ),
}
COLOURS = {"FAR-L": (200, 120, 40), "FAR-R": (60, 170, 230), "NEAR-L": (90, 180, 90), "NEAR-R": (60, 90, 220)}  # BGR


@dataclass
class Camera:
    name: str
    pos: np.ndarray       # metres
    yaw: float            # degrees from straight across, + right
    tilt: float           # degrees down
    lens: str

    def __post_init__(self) -> None:
        hfov, vfov = LENSES[self.lens]
        self.f = W_PX / math.radians(hfov)                      # pixels per radian
        self.h = int(round(self.f * math.radians(vfov)))
        y, t = math.radians(self.yaw), math.radians(self.tilt)
        self.fwd = np.array([math.sin(y) * math.cos(t), math.cos(y) * math.cos(t), -math.sin(t)])
        right = np.cross(self.fwd, [0.0, 0.0, 1.0])
        self.right = right / np.linalg.norm(right)
        self.down = np.cross(self.fwd, self.right)

    def project(self, P: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixels of world points (n, 3) and whether each lands in frame."""
        d = P - self.pos
        d = d / np.linalg.norm(d, axis=-1, keepdims=True)
        xc, yc, zc = d @ self.right, d @ self.down, d @ self.fwd
        theta = np.arccos(np.clip(zc, -1, 1))
        phi = np.arctan2(yc, xc)
        uv = np.stack([W_PX / 2 + self.f * theta * np.cos(phi), self.h / 2 + self.f * theta * np.sin(phi)], -1)
        ok = (theta < math.radians(95)) & (uv[:, 0] >= 0) & (uv[:, 0] < W_PX) & (uv[:, 1] >= 0) & (uv[:, 1] < self.h)
        return uv, ok


def rig(mast: float, setback: float, width: float, head: str = "milesight") -> list[Camera]:
    pos = np.array([0.0, -width / 2 - setback, mast])
    return [Camera(n, pos, y, t, l) for n, y, t, l in HEADS[head]]


def grid(length: float, width: float, margin: float, step: float) -> np.ndarray:
    xs = np.arange(-length / 2 - margin, length / 2 + margin + 1e-9, step)
    ys = np.arange(-width / 2 - margin, width / 2 + margin + 1e-9, step)
    X, Y = np.meshgrid(xs, ys)
    return np.stack([X.ravel(), Y.ravel()], 1)


def per_point(cams: list[Camera], xy: np.ndarray, ball_d: float, player_h: float) -> dict[str, np.ndarray]:
    """For each ground point, the camera that shows the ball largest, and
    what that camera gives: ball px, player px, metres per pixel of foot
    position in the worst direction."""
    n = len(xy)
    ground = np.concatenate([xy, np.zeros((n, 1))], 1)
    out = {k: np.full(n, np.nan) for k in ("ball", "player", "m_per_px", "cam", "score")}
    eps = 0.05
    for i, c in enumerate(cams):
        p0, ok = c.project(ground)
        jx = (c.project(ground + [eps, 0, 0])[0] - c.project(ground - [eps, 0, 0])[0]) / (2 * eps)
        jy = (c.project(ground + [0, eps, 0])[0] - c.project(ground - [0, eps, 0])[0]) / (2 * eps)
        a, b, cc, d = jx[:, 0], jy[:, 0], jx[:, 1], jy[:, 1]
        s1 = a * a + b * b + cc * cc + d * d
        s2 = np.sqrt(np.maximum((a * a + b * b - cc * cc - d * d) ** 2 + 4 * (a * cc + b * d) ** 2, 0))
        sig_min = np.sqrt(np.maximum((s1 - s2) / 2, 1e-12))       # px per metre, worst direction
        # A resting ball's image is its angular size times the local scale.
        centre = ground + [0, 0, ball_d / 2]
        ray = centre - c.pos
        ray /= np.linalg.norm(ray, axis=1, keepdims=True)
        e1 = np.cross(ray, [0.0, 0.0, 1.0])
        e1 /= np.linalg.norm(e1, axis=1, keepdims=True)
        e2 = np.cross(e1, ray)
        r = ball_d / 2
        ball = 0.5 * (np.linalg.norm(c.project(centre + r * e1)[0] - c.project(centre - r * e1)[0], axis=1)
                      + np.linalg.norm(c.project(centre + r * e2)[0] - c.project(centre - r * e2)[0], axis=1))
        player = np.linalg.norm(c.project(ground + [0, 0, player_h])[0] - p0, axis=1)
        # Where two cameras overlap they show the ball within a few percent of
        # each other; prefer the one that sees the point nearer its centre.
        theta = np.degrees(np.arccos(np.clip(ray @ c.fwd, -1, 1)))
        score = ball * (1 - 0.002 * theta)
        take = ok & (np.isnan(out["score"]) | (score > out["score"]))
        out["score"][take] = score[take]
        out["ball"][take] = ball[take]
        out["player"][take] = player[take]
        out["m_per_px"][take] = 1.0 / sig_min[take]
        out["cam"][take] = i
    return out


def coverage(cams: list[Camera], xy: np.ndarray, head_h: float = 1.8) -> float:
    """Share of ground points whose feet and head are both seen by some camera."""
    need = np.concatenate([np.concatenate([xy, np.zeros((len(xy), 1))], 1),
                           np.concatenate([xy, np.full((len(xy), 1), head_h)], 1)])
    ok = np.zeros(len(need), bool)
    for c in cams:
        ok |= c.project(need)[1]
    return float(ok.reshape(2, -1).all(axis=0).mean())


def report(args: argparse.Namespace) -> tuple[list[Camera], np.ndarray, dict[str, np.ndarray]]:
    cams = rig(args.mast, args.setback, args.width, args.head)
    xy = grid(args.length, args.width, 0.0, 1.0)
    m = per_point(cams, xy, args.ball, args.player)
    cov = coverage(cams, grid(args.length, args.width, args.margin, 1.0))
    L, W = args.length / 2, args.width / 2
    def at(x, y):
        return int(np.argmin(np.linalg.norm(xy - [x, y], axis=1)))
    worst = int(np.nanargmin(m["ball"]))
    print(f"{args.head} head; pitch {args.length:g} x {args.width:g} m, mast {args.mast:g} m, {args.setback:g} m behind the touchline")
    print(f"  in frame, feet and heads, pitch plus {args.margin:g} m: {cov * 100:.1f}%")
    print(f"  smallest ball {m['ball'][worst]:.1f} px at ({xy[worst, 0]:+.0f}, {xy[worst, 1]:+.0f}); "
          f"far corner {m['ball'][at(-L, W)]:.1f} px; centre spot {m['ball'][at(0, 0)]:.1f} px")
    print(f"  smallest player {np.nanmin(m['player']):.0f} px; worst foot position {np.nanmax(m['m_per_px']):.2f} m per pixel")
    for i, c in enumerate(cams):
        print(f"  {c.name:<6} {c.lens:<5} yaw {c.yaw:+5.1f} tilt {c.tilt:4.1f}: serves {np.mean(m['cam'] == i) * 100:4.1f}% of the pitch")
    return cams, xy, m


# --------------------------------------------------------------- drawings

def pitch_polylines(length: float, width: float) -> list[tuple[str, np.ndarray]]:
    """The painted lines as sampled polylines, from the pipeline's own pitch model."""
    from sideline.sports import get_sport

    surface = get_sport("soccer").surface(length, width)
    out = []
    for line in surface.lines():
        t = np.linspace(0, 1, max(2, int(line.length / 0.25)))[:, None]
        out.append((line.name, np.array(line.p0) * (1 - t) + np.array(line.p1) * t))
    for c in surface.circles():
        a = np.linspace(0, 2 * math.pi, 240)
        out.append((c.name, np.stack([c.centre[0] + c.radius * np.cos(a), c.centre[1] + c.radius * np.sin(a)], 1)))
    return out


def aim_card(cam: Camera, lines: list[tuple[str, np.ndarray]], path: Path, scale: float = 1 / 3) -> None:
    import cv2

    w, h = int(W_PX * scale), int(cam.h * scale)
    img = np.full((h, w, 3), (58, 92, 58), np.uint8)
    for name, pts in lines:
        P = np.concatenate([pts, np.zeros((len(pts), 1))], 1)
        uv, ok = cam.project(P)
        uv = uv * scale
        # Draw only unbroken runs inside the frame.
        idx = np.flatnonzero(ok)
        if len(idx) < 2:
            continue
        for run in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1):
            if len(run) > 1:
                colour = (255, 255, 255) if "goal_area" not in name else (200, 200, 200)
                cv2.polylines(img, [np.round(uv[run]).astype(np.int32)], False, colour, 2, cv2.LINE_AA)
    # A standing player every 10 m along the far touchline, so the heads'
    # headroom shows on the far cameras.
    cv2.putText(img, f"{cam.name}  {cam.lens}  yaw {cam.yaw:+.1f}  tilt {cam.tilt:.1f} down", (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(img, "expected view: trim the swivel until the live lines sit on these", (12, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)


def coverage_map(cams: list[Camera], length: float, width: float, m: dict[str, np.ndarray], xy: np.ndarray,
                 lines: list[tuple[str, np.ndarray]], path: Path, ppm: int = 8) -> None:
    import cv2

    pad = 6
    W, H = int((length + 2 * pad) * ppm), int((width + 2 * pad) * ppm)
    img = np.full((H + 60, W, 3), 245, np.uint8)
    def px(x, y):
        return int(round((x + length / 2 + pad) * ppm)), int(round((width / 2 + pad - y) * ppm))
    ball_lo, ball_hi = 6.0, 22.0
    for (x, y), ci, b in zip(xy, m["cam"], m["ball"]):
        if np.isnan(ci):
            continue
        base = np.array(COLOURS[cams[int(ci)].name], float)
        shade = 0.45 + 0.55 * np.clip((b - ball_lo) / (ball_hi - ball_lo), 0, 1)   # darker = smaller ball
        u, v = px(x - 0.5, y + 0.5)
        cv2.rectangle(img, (u, v), (u + ppm, v + ppm), tuple(int(c) for c in base * shade), -1)
    for _, pts in lines:
        cv2.polylines(img, [np.array([px(x, y) for x, y in pts], np.int32)], False, (255, 255, 255), 1, cv2.LINE_AA)
    back = -cams[0].pos[1] - width / 2
    cu, cv_ = px(0, -width / 2 - pad + 0.8)
    cv2.circle(img, (cu, cv_), 5, (0, 0, 0), -1)
    cv2.putText(img, f"mast {back:g} m back, {cams[0].pos[2]:g} m up", (cu + 8, cv_ + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                (0, 0, 0), 1, cv2.LINE_AA)
    x0 = 10
    for c in cams:
        cv2.rectangle(img, (x0, H + 10), (x0 + 16, H + 26), COLOURS[c.name], -1)
        cv2.putText(img, f"{c.name} {c.lens}", (x0 + 22, H + 23), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1, cv2.LINE_AA)
        x0 += 150
    cv2.putText(img, f"darker = smaller ball ({ball_lo:.0f} px), lighter = {ball_hi:.0f} px or more", (10, H + 48),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--head", choices=sorted(HEADS), default="milesight",
                    help="milesight: the open head; reolink-833a: the hidden pod")
    ap.add_argument("--mast", type=float, default=8.0, help="camera height above the grass, metres")
    ap.add_argument("--setback", type=float, default=10.0, help="mast distance behind the touchline, metres")
    ap.add_argument("--length", type=float, default=100.0)
    ap.add_argument("--width", type=float, default=64.0)
    ap.add_argument("--margin", type=float, default=2.0, help="ground outside the lines that must be in frame")
    ap.add_argument("--ball", type=float, default=0.22, help="ball diameter, m (size 4: 0.205)")
    ap.add_argument("--player", type=float, default=1.6, help="player height, m")
    ap.add_argument("--out", type=Path, help="write aim cards and a coverage map here")
    args = ap.parse_args()
    cams, xy, m = report(args)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        lines = pitch_polylines(args.length, args.width)
        for c in cams:
            aim_card(c, lines, args.out / f"aim_{c.name.lower()}.png")
        coverage_map(cams, args.length, args.width, m, xy, lines, args.out / "coverage.png")
        print(f"  wrote {len(cams)} aim cards and coverage.png to {args.out}")


if __name__ == "__main__":
    main()
