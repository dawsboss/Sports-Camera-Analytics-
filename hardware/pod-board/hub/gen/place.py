"""Greedy placement for the hub's small parts.

Each part has an anchor (a pad it connects to, or a point); it goes to the
nearest spot, searched outward in a spiral, whose courtyard clears the
board edge, the fixed connectors, the hand-routed MIPI copper and every
part placed before it.
"""
from __future__ import annotations

import math

import pcbnew

import hub_design as D

MM = pcbnew.FromMM
TO = pcbnew.ToMM

# Where each part wants to be: (ref, pad) of what it serves, or an (x, y) point.
ANCHOR = {
    # ICs
    "U1": (141.5, 117.0), "U2": (152.5, 115.5), "U8": (124.0, 127.0), "U9": (166.0, 127.0),
    "U3": ("P1", "22", 0, -6.5), "U4": ("P2", "22", 1.5, -6.5), "U5": ("P3", "22", 0, -6.5), "U6": ("P4", "22", 0, -6.5),
    "U7": (146.0, 125.5), "JP1": ("U7", "5", 0, 3.0), "U10": (146.5, 132.0), "U11": (150.5, 137.5),
    # decoupling and switch parts
    "C10": ("U3", "1"), "C11": ("U3", "4"), "C12": ("U3", "6"), "C13": ("U3", "6"), "R50": ("U3", "5"),
    "C14": ("U4", "1"), "C15": ("U4", "4"), "C16": ("U4", "6"), "C17": ("U4", "6"), "R51": ("U4", "5"),
    "C18": ("U5", "1"), "C19": ("U5", "4"), "C20": ("U5", "6"), "C21": ("U5", "6"), "R52": ("U5", "5"),
    "C22": ("U6", "1"), "C23": ("U6", "4"), "C24": ("U6", "6"), "C25": ("U6", "6"), "R53": ("U6", "5"),
    "C30": ("U7", "1"), "C31": ("U7", "5"), "C32": ("J1", "47", 0, 7.0), "C33": ("J2", "47", 0, 7.0),
    "C40": ("U1", "24"), "C41": ("U2", "24"), "C42": ("U8", "16"), "C43": ("U8", "15"), "C44": ("U9", "16"), "C45": ("U9", "15"),
    "C50": ("U10", "8"), "C51": ("U10", "8"), "C52": ("U10", "5"), "C53": ("U11", "3"),
    # resistors
    "R1": ("U1", "1"), "R2": ("U2", "1"), "R3": ("U10", "4"), "R7": ("U10", "1"), "R8": ("U10", "9"),
    "R30": ("U10", "9"), "R31": ("U8", "4"), "R32": ("U10", "9"), "R33": ("J3", "1", 0, 5.0), "R34": ("J3", "3", 0, 5.0),
    "R40": ("J4", "4"), "R41": ("J5", "4"), "R42": ("J6", "4"), "R43": ("J7", "4"),
    "R4": ("D1", "2"), "R5": ("D2", "2"), "R6": ("D3", "2"),
    "D1": ("U1", "14", 0, 6.0), "D2": ("U2", "14", 0, 6.0), "D3": (146.0, 121.0),
    "TP1": (140.0, 136.0), "TP2": (140.0, 139.0), "TP3": (143.0, 139.0), "TP4": (152.5, 125.0), "TP5": (143.0, 136.0), "TP6": (152.5, 128.0),
    "H1": (108.8, 113.0), "H2": (144.5, 141.0), "H3": (171.5, 114.5),
}
for i, h in enumerate(("FARL", "NEARL", "FARR", "NEARR")):
    ref = "U8" if h in ("FARL", "NEARL") else "U9"
    base = 0 if h in ("FARL", "FARR") else 2
    rn = 10 + (8 if ref == "U9" else 0) + base * 2
    # A side (bus, 47 ohm) and B side (head, 33 ohm) resistors for XVS and
    # XHS, set out from their pins (A pins face +x, B pins -x) so that the
    # router has room to fan the 0.65 mm pin pitch out to them
    ANCHOR[f"R{rn}"] = (ref, str(3 + base), 2.4, 0.0)
    ANCHOR[f"R{rn + 1}"] = (ref, str(14 - base), -2.4, 0.0)
    ANCHOR[f"R{rn + 2}"] = (ref, str(4 + base), 2.4, 0.0)
    ANCHOR[f"R{rn + 3}"] = (ref, str(13 - base), -2.4, 0.0)

ORDER = ["H1", "H2", "H3", "U1", "U2", "U8", "U9", "U3", "U4", "U5", "U6", "U7", "U10", "U11", "JP1", "D1", "D2", "D3"]


class Placer:
    def __init__(self, board, fps, x0, y0, x1, y1, margin=0.35, gap=0.15):
        self.b, self.fps = board, fps
        self.lim = (x0 + margin, y0 + margin, x1 - margin, y1 - margin)
        self.gap = gap
        self.rects = []          # placed courtyards
        self.caps = []           # (x1, y1, x2, y2, r) capsules for tracks
        self.circles = []        # vias
        self.hole_caps = []      # copper a drilled hole must also clear: every layer
        self.hole_circles = []

    def add_fixed(self, fp):
        self.rects.append(self._crt(fp))

    def add_copper(self, items, clearance=0.2, holes_only=False):
        circles, caps = (self.hole_circles, self.hole_caps) if holes_only else (self.circles, self.caps)
        for it in items:
            if isinstance(it, pcbnew.PCB_VIA):
                p = it.GetPosition()
                circles.append((TO(p.x), TO(p.y), TO(it.GetWidth(pcbnew.F_Cu)) / 2 + clearance))
            else:
                s, e = it.GetStart(), it.GetEnd()
                caps.append((TO(s.x), TO(s.y), TO(e.x), TO(e.y), TO(it.GetWidth()) / 2 + clearance))

    @staticmethod
    def _crt(fp):
        """The courtyard, grown to cover the part's silkscreen outline so
        neighbours' silk does not overlap either."""
        layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
        silk = pcbnew.B_SilkS if fp.IsFlipped() else pcbnew.F_SilkS
        bb = fp.GetCourtyard(layer).BBox()
        if bb.GetWidth() == 0:
            bb = fp.GetBoundingBox(False)
        for g in fp.GraphicalItems():
            if g.GetLayer() == silk and not isinstance(g, pcbnew.PCB_TEXT):
                bb.Merge(g.GetBoundingBox())
        return (TO(bb.GetX()), TO(bb.GetY()), TO(bb.GetRight()), TO(bb.GetBottom()))

    def _free(self, r, hole=False):
        x1, y1, x2, y2 = r
        lx1, ly1, lx2, ly2 = self.lim
        if x1 < lx1 or y1 < ly1 or x2 > lx2 or y2 > ly2:
            return False
        g = self.gap
        for a in self.rects:
            if x1 < a[2] + g and x2 > a[0] - g and y1 < a[3] + g and y2 > a[1] - g:
                return False
        circles = self.circles + (self.hole_circles if hole else [])
        caps = self.caps + (self.hole_caps if hole else [])
        for (cx, cy, rad) in circles:
            if _rect_point_dist(r, cx, cy) < rad:
                return False
        for (ax, ay, bx, by, rad) in caps:
            if _rect_seg_dist(r, ax, ay, bx, by) < rad:
                return False
        return True

    def place(self, ref, target, rots=(0, 90)):
        fp = self.fps[ref]
        tx, ty = target
        for radius in _spiral(3.0 if ref.startswith(("U", "H", "J")) else 5.0):
            for (dx, dy) in radius:
                for rot in rots:
                    fp.SetOrientationDegrees(180 + rot)
                    fp.SetPosition(pcbnew.VECTOR2I(MM(tx + dx), MM(ty + dy)))
                    r = self._crt(fp)
                    if self._free(r, hole=ref.startswith("H")):
                        self.rects.append(r)
                        return True
        raise RuntimeError(f"no room for {ref} near {target}")


def _spiral(limit, step=0.25):
    yield [(0.0, 0.0)]
    n = 1
    while n * step <= limit * 4:
        ring = []
        d = n * step
        k = max(8, int(2 * math.pi * d / step))
        for i in range(k):
            a = 2 * math.pi * i / k
            ring.append((round(d * math.cos(a) / 0.05) * 0.05, round(d * math.sin(a) / 0.05) * 0.05))
        yield ring
        n += 1


def _rect_point_dist(r, px, py):
    x1, y1, x2, y2 = r
    dx = max(x1 - px, 0, px - x2)
    dy = max(y1 - py, 0, py - y2)
    return math.hypot(dx, dy)


def _seg_seg_dist(a, b, c, d):
    def dot(u, v): return u[0] * v[0] + u[1] * v[1]
    def sub(u, v): return (u[0] - v[0], u[1] - v[1])
    def pt_seg(p, s0, s1):
        v = sub(s1, s0); w = sub(p, s0)
        L = dot(v, v)
        t = 0 if L == 0 else max(0, min(1, dot(w, v) / L))
        q = (s0[0] + t * v[0], s0[1] + t * v[1])
        return math.hypot(p[0] - q[0], p[1] - q[1])
    def ccw(p, q, r): return (r[1] - p[1]) * (q[0] - p[0]) > (q[1] - p[1]) * (r[0] - p[0])
    if ccw(a, c, d) != ccw(b, c, d) and ccw(a, b, c) != ccw(a, b, d):
        return 0.0
    return min(pt_seg(a, c, d), pt_seg(b, c, d), pt_seg(c, a, b), pt_seg(d, a, b))


def _rect_seg_dist(r, ax, ay, bx, by):
    x1, y1, x2, y2 = r
    if x1 <= ax <= x2 and y1 <= ay <= y2:
        return 0.0
    edges = [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]
    return min(_seg_seg_dist((ax, ay), (bx, by), e0, e1) for e0, e1 in edges)


def anchor_point(fps, a):
    if isinstance(a[0], str):
        ref, pad = a[0], a[1]
        dx, dy = (a[2], a[3]) if len(a) > 2 else (0.0, 0.0)
        for p in fps[ref].Pads():
            if p.GetNumber() == pad:
                return TO(p.GetPosition().x) + dx, TO(p.GetPosition().y) + dy
        raise KeyError(a)
    return a


def run(board, fps, fixed_refs, mipi_items, frame, hole_items=()):
    """Place every part not in fixed_refs. mipi_items is the copper parts
    must clear; hole_items is copper only the mounting holes must also
    clear, since a drilled hole passes through every layer."""
    pl = Placer(board, fps, *frame)
    for ref in fixed_refs:
        pl.add_fixed(fps[ref])
    pl.add_copper(mipi_items)
    pl.add_copper(hole_items, holes_only=True)
    todo = ORDER + [p.ref for p in D.PARTS if p.ref not in ORDER and p.ref not in fixed_refs]
    for ref in todo:
        pl.place(ref, anchor_point(fps, ANCHOR[ref]))
    return pl
