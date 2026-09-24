"""Check the pod against its cameras before printing it.

    python hardware/pod/check_pod.py              # parameters from the .scad, meshes from stl/
    python hardware/pod/check_pod.py --export     # re-export every part with OpenSCAD first

1. Windows: each window is a face of the pod's hull, so nothing of the pod
   stands in front of a window's plane. Arithmetic on the .scad's own
   parameters; no meshes.
2. Pieces: every printed piece one watertight body that fits the bed.
3. Views: no point of the shell or the frame inside any camera's view,
   widened by the swivel trim either way.
4. Clearances: turrets clear of each other, the shell and the frame; the
   frame clear of the shell; no piece inside another.

Needs numpy, trimesh and scipy (pip install trimesh scipy rtree), and OpenSCAD
only with --export. Change a parameter in sideline_pod.scad, re-export,
and run this before printing anything.
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCAD = HERE / "sideline_pod.scad"
SHELL = ("shell_cap", "shell_ul", "shell_ur", "shell_ll", "shell_lr")
FRAME = ("frame_upper", "frame_lower")
OTHER = ("switch_mount", "pad_test", "window_test")

# The Reolink RLC-833A's sheet fields of view at each end of its zoom,
# interpolated linearly between (the same model rig_geometry.py uses).
def zoom_fov(hfov: float) -> tuple[float, float]:
    return hfov, 30 + (hfov - 50) * 23 / 44


def scad_params(path: Path = SCAD) -> dict:
    """Top-level `name = value;` assignments, evaluated in order."""
    env: dict = {"true": True, "false": False, "sqrt": math.sqrt}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z_]\w*)\s*=\s*([^;]+);", line)
        if not m:
            continue
        try:
            env[m.group(1)] = eval(m.group(2), {"__builtins__": {}}, env)   # numbers, vectors, simple sums
        except Exception:
            pass
    return env


def direction(yaw: float, tilt: float) -> np.ndarray:
    y, t = math.radians(yaw), math.radians(tilt)
    return np.array([math.sin(y) * math.cos(t), math.cos(y) * math.cos(t), -math.sin(t)])


def cameras(P: dict) -> list[dict]:
    out = []
    for kind in ("far", "near"):
        for side, name in ((-1, "L"), (1, "R")):
            p = np.array(P[f"{kind}_pos"], float) * [side, 1, 1]
            out.append(dict(name=f"{kind}-{name}", kind=kind, pad=p, d=direction(side * P[f"{kind}_yaw"], P[f"{kind}_tilt"]),
                            gap=P[f"{kind}_gap"]))
    return out


# ------------------------------------------------------------ 1. flush windows

def flush_windows(P: dict) -> float:
    """How far the nearest other part of the hull stands in front of each
    window's plane (negative: behind it). The hull's generators are the
    ones pod_solid() hulls in the .scad."""
    cams = cameras(P)
    base_r = P["tur_base_d"] / 2 + P["base_clear"]
    def disc(u, c, n, r):
        return u @ c + r * math.sqrt(max(0.0, 1 - (u @ n) ** 2))
    def cyl(u, a, b, r):
        ax = (b - a) / np.linalg.norm(b - a)
        return max(u @ a, u @ b) + r * math.sqrt(max(0.0, 1 - (u @ ax) ** 2))
    rx, ry0, ry1, rz = P["roof"]
    pts = [np.array([x, y, rz]) for x in (-rx, rx) for y in (ry0, ry1)]
    pts += [np.array([x, P["back_y"], z]) for x in (-rx, rx) for z in P["back_z"]]
    pts += [np.array([P["floor_r"] * math.cos(a), P["floor_r"] * math.sin(a), P["floor_z"]])
            for a in np.linspace(0, 2 * math.pi, 180, endpoint=False)]
    pts = np.array(pts)
    worst = -np.inf
    print("1. windows (nothing may stand within 1 mm of a window's plane)")
    for c in cams:
        u = c["d"]
        own = u @ (c["pad"] + (P["tur_front"] + c["gap"]) * u)
        others = [("roof, back or floor", float(np.max(pts @ u)))]
        for o in cams:
            if o is not c:
                others.append((f"{o['name']} window", disc(u, o["pad"] + (P["tur_front"] + o["gap"]) * o["d"], o["d"], P["facet_r"])))
            a = o["pad"] - P["pad_t"] * o["d"]
            b = o["pad"] + (P["tur_base_h"] + 4) * o["d"]
            others.append((f"{o['name']} base", cyl(u, a, b, base_r)))
        name, h = max(others, key=lambda t: t[1])
        worst = max(worst, h - own)
        print(f"   {c['name']:<7} nearest: {name:<20} {h - own:+6.1f} mm")
    print(f"   {'ok' if worst <= -1 else 'FAIL'}: worst {worst:+.1f} mm")
    return worst


# ------------------------------------------------------------ 2 and 3, on meshes

def export(parts: tuple[str, ...], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    procs = [subprocess.Popen(["openscad", "--export-format", "binstl", "-D", f'part="{p}"', "-o", str(out / f"{p}.stl"), str(SCAD)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) for p in parts]
    for p, proc in zip(parts, procs):
        _, err = proc.communicate()
        if proc.returncode or "WARNING" in err:
            print(f"   {p}: openscad {'failed' if proc.returncode else 'warned'}:\n{err[-600:]}")


def turret_points(P: dict, c: dict, n: int = 6000, skip_seat: float = 2.0) -> np.ndarray:
    """Points on the turret's surface; the base's underside, which sits on
    the seat, is left out."""
    rng = np.random.default_rng(0)
    d = c["d"]
    a = np.cross(d, [0, 0, 1.0]); a /= np.linalg.norm(a); b = np.cross(d, a)
    th = rng.uniform(0, 2 * math.pi, n)
    s = rng.uniform(skip_seat, P["tur_base_h"], n // 3)
    base = c["pad"] + s[:, None] * d + (P["tur_base_d"] / 2) * (np.cos(th[: n // 3, None]) * a + np.sin(th[: n // 3, None]) * b)
    u = rng.normal(size=(n, 3)); u /= np.linalg.norm(u, axis=1, keepdims=True)
    ball = c["pad"] + P["tur_ball_c"] * d + (P["tur_ball_d"] / 2) * u
    ball = ball[(ball - c["pad"]) @ d > P["tur_base_h"]]
    rr = np.sqrt(rng.uniform(0, 1, n // 3)) * P["tur_base_d"] / 2
    top = c["pad"] + P["tur_base_h"] * d + rr[:, None] * (np.cos(th[: n // 3, None]) * a + np.sin(th[: n // 3, None]) * b)
    return np.concatenate([base, ball, top])


def f_theta_in_view(pts: np.ndarray, lens: np.ndarray, d: np.ndarray, hfov: float, vfov: float) -> np.ndarray:
    right = np.cross(d, [0, 0, 1.0]); right /= np.linalg.norm(right); down = np.cross(d, right)
    v = pts - lens
    dist = np.linalg.norm(v, axis=1)
    v = v / dist[:, None]
    theta = np.arccos(np.clip(v @ d, -1, 1))
    phi = np.arctan2(v @ down, v @ right)
    # f-theta: angle from the axis maps linearly to image radius.
    x, y = np.degrees(theta) * np.cos(phi), np.degrees(theta) * np.sin(phi)
    return (np.abs(x) <= hfov / 2) & (np.abs(y) <= vfov / 2) & ((pts - lens) @ d > 1.0)


def meshes(P: dict, stl: Path, bed: float, trim: float) -> bool:
    import trimesh
    from scipy.spatial import cKDTree

    ok = True
    parts = {}
    print(f"\n2. pieces (bed {bed:.0f} mm)")
    for name in SHELL + FRAME + OTHER:
        f = stl / f"{name}.stl"
        if not f.exists():
            print(f"   {name}: missing, run with --export"); ok = False; continue
        m = trimesh.load(f)
        parts[name] = m
        bodies = len(m.split(only_watertight=False))
        ext = np.sort(m.extents)[::-1]
        fits = ext[0] <= bed and ext[1] <= bed and ext[2] <= bed
        good = m.is_watertight and bodies == 1 and fits
        ok &= good
        print(f"   {name:<13} {'ok  ' if good else 'FAIL'} {'watertight' if m.is_watertight else 'NOT WATERTIGHT'}, "
              f"{bodies} bod{'y' if bodies == 1 else 'ies'}, {ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm, "
              f"{m.volume / 1000 * 1.07:.0f} g solid ASA")
    shell = [parts[n] for n in SHELL if n in parts]
    frame = [parts[n] for n in FRAME if n in parts]
    if len(shell) < len(SHELL) or len(frame) < len(FRAME):
        return False
    rng = np.random.default_rng(1)
    def surface(ms, n, vertices=False):
        return np.concatenate([np.concatenate([trimesh.sample.sample_surface(m, n, seed=int(rng.integers(1 << 30)))[0]]
                                              + ([m.vertices] if vertices else [])) for m in ms])
    def inside(ms, q):
        n = 0
        for m in ms:
            sel = np.all((q >= m.bounds[0]) & (q <= m.bounds[1]), axis=1)
            n += int(m.contains(q[sel]).sum()) if sel.any() else 0
        return n
    # Vertices too: they are where a part's extreme edges are.
    shell_pts, frame_pts = surface(shell, 60000, True), surface(frame, 60000, True)

    print(f"\n3. views (each camera's frame widened by {trim:g} deg of trim either way)")
    for c in cameras(P):
        hf, vf = zoom_fov(P[f"{c['kind']}_hfov"])
        lens = c["pad"] + (P["tur_front"] - 2) * c["d"]
        n_s = int(f_theta_in_view(shell_pts, lens, c["d"], hf + 2 * trim, vf + 2 * trim).sum())
        n_f = int(f_theta_in_view(frame_pts, lens, c["d"], hf + 2 * trim, vf + 2 * trim).sum())
        ok &= n_s == 0 and n_f == 0
        print(f"   {c['name']:<7} {hf:.0f} x {vf:.1f} deg: {n_s} shell points, {n_f} frame points in view")

    print("\n4. clearances")
    # Turret to turret, each against the other's exact shape (a base
    # cylinder and a ball), both ways round.
    def clear(q, c):
        rel = q - c["pad"]; s = rel @ c["d"]; radial = np.linalg.norm(rel - np.outer(s, c["d"]), axis=1)
        along = np.maximum(-s, 0) + np.maximum(s - P["tur_base_h"], 0)
        cyl = np.hypot(along, np.maximum(radial - P["tur_base_d"] / 2, 0))
        cyl = np.where((s >= 0) & (s <= P["tur_base_h"]), np.maximum(radial - P["tur_base_d"] / 2, -1.0), cyl)
        ball = np.linalg.norm(q - (c["pad"] + P["tur_ball_c"] * c["d"]), axis=1) - P["tur_ball_d"] / 2
        return np.minimum(cyl, ball)
    cs = cameras(P)
    tt = min(clear(turret_points(P, a, 4000, 0.0), b).min() for a in cs for b in cs if a is not b)
    ok &= tt >= 5.0
    print(f"   turret to turret {tt:.1f} mm")
    shell_tree = cKDTree(surface(shell, 400000))
    frame_tree = cKDTree(surface(frame, 400000))
    tur = np.concatenate([turret_points(P, c, 2500) for c in cameras(P)])
    n_in = inside(shell + frame, tur)
    ds, df = shell_tree.query(tur)[0].min(), frame_tree.query(tur)[0].min()
    ok &= n_in == 0 and ds >= 2.0 and df >= 1.0
    print(f"   turrets to shell {ds:.1f} mm, to frame {df:.1f} mm (seat faces aside); turret points inside a part: {n_in}")
    # The floor ring sits on the floor and the top plate under the roof, by
    # design; everywhere else the frame must stand clear of the shell.
    z0, zt = P["floor_z"] + P["wall"], P["roof"][3] - P["wall"] - 6
    fp = surface(frame, 15000)
    n_in = inside(shell, fp[fp[:, 2] > z0 + 0.5])
    free = fp[(fp[:, 2] > z0 + 6.5) & (fp[:, 2] < zt - 0.5)]
    dfs = shell_tree.query(free)[0].min()
    ok &= n_in == 0 and dfs >= 1.0
    print(f"   frame to shell {dfs:.1f} mm between the floor ring and the top plate; frame points inside the shell: {n_in}")
    # The halves meet only at the core's joint face.
    up = surface([parts["frame_upper"]], 30000)
    at_joint = ((np.abs(up[:, 0]) <= P["core_x"] + 1) & (up[:, 1] >= P["core_yb"] - 1) & (up[:, 1] <= P["core_yf"] + 1)
                & (up[:, 2] < P["split_z"] + 3))
    gap = cKDTree(surface([parts["frame_lower"]], 200000)).query(up[~at_joint])[0].min()
    ok &= gap >= 1.0
    print(f"   frame halves apart from their joint face: {gap:.1f} mm")
    # Pieces touch only at their butt joints; a tongue must never be inside
    # the wall it hides behind.
    seams = lambda q: (np.abs(q[:, 0]) < 0.2) | (np.abs(q[:, 2] - P["split_z"]) < 0.2) | (np.abs(q[:, 2] - P["cap_z"]) < 0.2)
    clash = 0
    for a in SHELL:
        q = surface([parts[a]], 10000)
        clash += inside([parts[b] for b in SHELL if b != a], q[~seams(q)])
    ok &= clash == 0
    print(f"   shell pieces inside one another, away from the seams: {clash} points")
    total_shell = sum(m.volume for m in shell) / 1000 * 1.07
    walls = sum(min(m.area * 1.6, m.volume) + 0.25 * max(m.volume - m.area * 1.6, 0) for m in frame) / 1000 * 1.07
    print(f"\n   shell {total_shell:.0f} g (all wall), frame about {walls:.0f} g at four walls and 25% infill, ASA")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--stl", type=Path, default=HERE / "stl")
    ap.add_argument("--export", action="store_true", help="re-export every part with OpenSCAD first")
    ap.add_argument("--bed", type=float, default=250.0, help="largest print, mm on a side")
    ap.add_argument("--trim", type=float, default=5.0, help="swivel trim to allow either way, degrees")
    args = ap.parse_args()
    P = scad_params()
    ok = flush_windows(P) <= -1
    if args.export:
        print("\nexporting with OpenSCAD (a few minutes)")
        export(SHELL + FRAME + OTHER, args.stl)
    ok &= meshes(P, args.stl, args.bed, args.trim)
    print("\nall checks pass" if ok else "\nSOME CHECKS FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
