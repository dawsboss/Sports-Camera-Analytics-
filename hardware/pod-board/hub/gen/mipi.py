"""Hand-routed MIPI pairs: straight runs and 45 degree bends on F.Cu over
the In1 ground plane, 0.25 mm traces 0.25 mm apart (about 100 ohm
differential on JLC's 7628 four-layer stackup).

At the 50-pin end the pairs run CLK, D0, D1 left to right; at the camera
port they must arrive CLK, D1, D0. So CLK and D0 go straight across, and D1
runs beside D0 until just above the port, where it drops to B.Cu, crosses
under D0 and comes down between CLK and D0.
"""
from __future__ import annotations

import math

import pcbnew

MM = pcbnew.FromMM
HALF = 0.25          # half the centre-to-centre spacing of a pair
VIA_HALF = 0.45      # half the spacing of a via pair
TRACE = 0.25
VIA_D, VIA_DRILL = 0.6, 0.3


def V(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


class Router:
    def __init__(self, board, nets):
        self.b = board
        self.nets = nets
        self.gnd = nets["GND"]
        self.items = []

    def track(self, pts, net, layer, width=TRACE):
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                continue
            t = pcbnew.PCB_TRACK(self.b)
            t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2)); t.SetWidth(MM(width)); t.SetLayer(layer)
            t.SetNet(self.nets[net] if isinstance(net, str) else net); t.SetLocked(True)
            self.b.Add(t); self.items.append(t)

    def via(self, x, y, net):
        v = pcbnew.PCB_VIA(self.b)
        v.SetPosition(V(x, y)); v.SetWidth(MM(VIA_D)); v.SetDrill(MM(VIA_DRILL)); v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetNet(self.nets[net] if isinstance(net, str) else net); v.SetLocked(True)
        self.b.Add(v); self.items.append(v)

    def pair(self, name, center, layer, halves=None):
        """Route P and N either side of a centreline. `halves` gives the
        half-spacing at each vertex (default HALF). P sits on the -x side of
        a line running down the board, N on the +x side."""
        halves = halves or [HALF] * len(center)
        p_pts, n_pts = [], []
        for i, (x, y) in enumerate(center):
            nx, ny = _miter_normal(center, i)
            h = halves[i]
            p_pts.append((x + nx * h, y + ny * h))
            n_pts.append((x - nx * h, y - ny * h))
        self.track(p_pts, f"{name}_P", layer)
        self.track(n_pts, f"{name}_N", layer)
        return p_pts, n_pts


def _unit(ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    return dx / L, dy / L


def _miter_normal(pts, i):
    """Normal pointing to the P side, (-dy, dx), so a line heading +y puts P
    at -x; mitred at corners so both traces keep their spacing in a bend."""
    segs = []
    if i > 0:
        segs.append(_unit(*pts[i - 1], *pts[i]))
    if i < len(pts) - 1:
        segs.append(_unit(*pts[i], *pts[i + 1]))
    normals = [(-d[1], d[0]) for d in segs]
    if len(normals) == 1:
        return normals[0]
    (ax, ay), (bx, by) = normals
    mx, my = ax + bx, ay + by
    L = math.hypot(mx, my)
    mx, my = mx / L, my / L
    cos = mx * ax + my * ay
    return mx / cos, my / cos


def head(r: Router, h: str, top: dict, bot: dict, s: int, y_top: float, y_bot: float,
         start_y: float, hop_y: float, d1_side_gap: float = 1.6):
    """Route one head. top/bot map 'CK','D0','D1' to pair centre x at the
    50-pin and at the camera port; s is the direction of travel (+1 right,
    -1 left).

    The long runs are on F.Cu over In1, so the component side (B.Cu) stays
    free for everything else to cross underneath. Each pair drops from its
    B.Cu pads to F.Cu through a via pair just inside the 50-pin (staggered
    rows, because the pads are only 0.5 mm apart), and comes back to B.Cu
    just above the camera port. D1 comes back early and crosses under D0 on
    B.Cu to reach its place between CLK and D0."""
    tc, t0, t1 = top["CK"], top["D0"], top["D1"]
    bc, b0, b1 = bot["CK"], bot["D0"], bot["D1"]
    F, B = pcbnew.F_Cu, pcbnew.B_Cu
    rows = {"D1": y_top + 1.9, "D0": y_top + 3.1, "CK": y_top + 4.3}
    # via pair centres at the top; D1 steps outward, clear of D0's stubs
    vtop = {"CK": tc, "D0": t0, "D1": t1 + 0.6 * (1 if t1 > t0 else -1)}
    tx = {"CK": tc, "D0": t0, "D1": t1}
    for name in ("CK", "D0", "D1"):
        y = rows[name]
        # B.Cu stubs from the pads to the via pair
        r.pair(f"{h}_{name}", [(tx[name], y_top), (tx[name], y - 0.6), (vtop[name], y)], B, [HALF, HALF, VIA_HALF])
        for sign, pol in ((-1, "P"), (1, "N")):
            r.via(vtop[name] + sign * VIA_HALF, y, f"{h}_{name}_{pol}")
    x1_run = b0 + d1_side_gap
    order = [("CK", vtop["CK"], bc), ("D0", vtop["D0"], b0), ("D1", vtop["D1"], x1_run)]
    lead = sorted(order, key=lambda o: -s * o[1])
    starts = {name: max(start_y, rows["CK"] + 1.2) + 1.2 * k for k, (name, _, _) in enumerate(lead)}
    ends = {}
    y_drop = y_bot - 2.3                    # CK and D0 return to B.Cu here
    for name, x_from, x_to in order:
        ys = starts[name]
        ye = ys + abs(x_to - x_from)
        ends[name] = ye
        y_last = hop_y if name == "D1" else y_drop
        pts = [(x_from, rows[name]), (x_from, rows[name] + 0.6), (x_from, ys), (x_to, ye), (x_to, y_last - 0.6), (x_to, y_last)]
        halves = [VIA_HALF, HALF, HALF, HALF, HALF, VIA_HALF]
        r.pair(f"{h}_{name}", pts, F, halves)
        for sign, pol in ((-1, "P"), (1, "N")):
            r.via(x_to + sign * VIA_HALF, y_last, f"{h}_{name}_{pol}")
    assert ends["D1"] < hop_y - 1.0, f"{h}: D1 diagonal ends at {ends['D1']:.2f}, too close to its drop at {hop_y}"
    # CK and D0: short B.Cu stubs down to the port
    for name, x in (("CK", bc), ("D0", b0)):
        r.pair(f"{h}_{name}", [(x, y_drop), (x, y_drop + 0.6), (x, y_bot)], B, [VIA_HALF, HALF, HALF])
    # D1: under D0 on B.Cu, then down between CLK and D0
    dy = abs(x1_run - b1)
    pts = [(x1_run, hop_y), (x1_run, hop_y + 0.6), (b1, hop_y + 0.6 + dy), (b1, y_bot)]
    r.pair(f"{h}_D1", pts, B, [VIA_HALF, HALF, HALF, HALF])
    # Ground vias beside the drops, on their outer side, so return current
    # can follow each layer change between In1 and In2.
    r.via(x1_run + 1.4, hop_y, "GND")
    r.via(x1_run + 1.4, hop_y + 1.3, "GND")
    r.via(b0 + 1.35, y_drop, "GND")
    r.via(bc - 1.35, y_drop, "GND")
    r.via(vtop["CK"] - 1.35, rows["CK"], "GND")
    r.via(vtop["D1"] + (1.35 if t1 > t0 else -1.35), rows["D1"], "GND")
    return {"x1_run": x1_run, "y_drop": y_drop}


def connector_ground(r: Router, fp, via_dy: float):
    """A via beside every ground pin of a connector, on its cable side (under
    the housing), stitched straight into both planes."""
    for pad in fp.Pads():
        if pad.GetNetname() != "GND" or not pad.GetNumber().isdigit():
            continue
        x, y = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
        r.track([(x, y), (x, y + via_dy)], "GND", pcbnew.B_Cu, width=0.25)
        r.via(x, y + via_dy, "GND")
