"""Planes, ground vias, autorouting and pours for the hub board.

In1 is a solid ground plane, the MIPI pairs' reference, and every ground
pad gets its own via into it. Freerouting then routes every other net on
F.Cu, In2 and B.Cu around the hand-routed MIPI copper, and is told to
leave ground alone. After import, F.Cu, In2 and B.Cu each get a ground
pour stitched to the plane, and tracks left with a free end are removed.

    FREEROUTING_JAR=/path/to/freerouting-1.9.0.jar python route.py
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).parent
OUT = HERE.parent
JAR = Path(os.environ.get("FREEROUTING_JAR", "freerouting.jar"))
MM = pcbnew.FromMM
TO = pcbnew.ToMM


def V(x, y):
    return pcbnew.VECTOR2I(MM(x), MM(y))


def zone(b, layer, net, frame, inset=0.3, clearance=0.2, min_w=0.2, thermal=True, priority=0, name=""):
    x0, y0, x1, y1 = frame
    z = pcbnew.ZONE(b)
    z.SetLayer(layer)
    z.SetNet(net)
    ol = z.Outline()
    ol.NewOutline()
    for (x, y) in ((x0 + inset, y0 + inset), (x1 - inset, y0 + inset), (x1 - inset, y1 - inset), (x0 + inset, y1 - inset)):
        ol.Append(MM(x), MM(y))
    z.SetLocalClearance(MM(clearance))
    z.SetMinThickness(MM(min_w))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL if thermal else pcbnew.ZONE_CONNECTION_FULL)
    z.SetThermalReliefGap(MM(0.25))
    z.SetThermalReliefSpokeWidth(MM(0.3))
    z.SetAssignedPriority(priority)
    if name:
        z.SetZoneName(name)
    b.Add(z)
    return z


# With In2 a plane, Freerouting 1.9.0 left three long runs unrouted (the
# NEAR-R head's I2C pair and one sync line), so In2 carries slow signals
# too and a ground pour fills the rest of it. make_pcb.py keeps its tracks
# out from under the MIPI pairs' B.Cu runs.
IN2_SIGNALS = True


def planes(b, frame):
    """In1 is a solid ground plane, the MIPI pairs' reference, and so is In2
    unless IN2_SIGNALS gives it to the router."""
    gnd = b.FindNet("GND")
    b.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    zone(b, pcbnew.In1_Cu, gnd, frame, thermal=False, name="GND_In1")
    if IN2_SIGNALS:
        b.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_SIGNAL)
    else:
        b.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_POWER)
        zone(b, pcbnew.In2_Cu, gnd, frame, thermal=False, name="GND_In2")


def pours(b, frame):
    gnd = b.FindNet("GND")
    zone(b, pcbnew.F_Cu, gnd, frame, clearance=0.25, priority=0, name="GND_F")
    if IN2_SIGNALS:
        zone(b, pcbnew.In2_Cu, gnd, frame, clearance=0.25, thermal=False, priority=0, name="GND_In2")
    zone(b, pcbnew.B_Cu, gnd, frame, clearance=0.25, priority=0, name="GND_B")


def _seg_rect_dist(ax, ay, bx, by, r):
    import place
    return place._rect_seg_dist(r, ax, ay, bx, by)


def fanout_gnd(b, via_d=0.6, drill=0.3, clearance=0.15, stub=0.25):
    """Give every ground pad its own via into the planes, placed at the
    nearest spot whose via and stub clear all other copper."""
    import place
    gnd = b.FindNet("GND")
    pads, tracks, vias = [], [], []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            pads.append((p, p.GetNetCode(), (TO(bb.GetX()), TO(bb.GetY()), TO(bb.GetRight()), TO(bb.GetBottom()))))
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            vias.append((TO(t.GetPosition().x), TO(t.GetPosition().y), TO(t.GetWidth(pcbnew.F_Cu)) / 2, t.GetNetCode()))
        else:
            tracks.append((TO(t.GetStart().x), TO(t.GetStart().y), TO(t.GetEnd().x), TO(t.GetEnd().y),
                           TO(t.GetWidth()) / 2, t.GetNetCode(), t.GetLayer()))
    bb = b.GetBoardEdgesBoundingBox()
    ex0, ey0, ex1, ey1 = TO(bb.GetX()) + 0.5, TO(bb.GetY()) + 0.5, TO(bb.GetRight()) - 0.5, TO(bb.GetBottom()) - 0.5
    rv = via_d / 2
    added = 0
    # rule areas that forbid vias or tracks: the board's screw-head keepouts
    # and footprints' own (the SHT45 keeps copper out from under itself)
    zones = list(b.Zones()) + [z for fp in b.GetFootprints() for z in fp.Zones()]
    keepouts = [z for z in zones if z.GetIsRuleArea() and (z.GetDoNotAllowVias() or z.GetDoNotAllowTracks())]
    outline = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(outline)
    edge = outline.Outline(0)
    edge_min = MM(rv + TO(b.GetDesignSettings().m_CopperEdgeClearance) + 0.05)

    def ok(vx, vy, px, py, pad_layer, own):
        if not (ex0 <= vx <= ex1 and ey0 <= vy <= ey1):
            return False
        if not outline.Contains(V(vx, vy)) or edge.Distance(V(vx, vy), True) < edge_min:
            return False            # the corners are rounded; the box test above is not enough
        for z in keepouts:
            if z.GetDoNotAllowVias() and z.Outline().Collide(V(vx, vy), MM(rv + 0.05)):
                return False
            # a keepout forbids overlap, not nearness: the SHT45's leaves only
            # a notch around each pad for the track to leave by
            if (z.GetDoNotAllowTracks() and z.IsOnLayer(pad_layer)
                    and z.Outline().Collide(pcbnew.SEG(V(px, py), V(vx, vy)), MM(stub / 2))):
                return False
        for (p, net, r) in pads:
            if p is own:
                continue
            if net == gnd.GetNetCode():
                if place._rect_point_dist(r, vx, vy) < rv + 0.05:
                    return False          # keep off other ground pads too, for solder
                continue
            if place._rect_point_dist(r, vx, vy) < rv + clearance:
                return False
            if p.IsOnLayer(pad_layer) and place._rect_seg_dist(r, px, py, vx, vy) < stub / 2 + clearance:
                return False
        for (x1, y1, x2, y2, hw, net, layer) in tracks:
            if net == gnd.GetNetCode():
                continue
            d = place._seg_seg_dist((vx, vy), (vx, vy), (x1, y1), (x2, y2))
            if d < rv + hw + clearance:
                return False
            if layer == pad_layer and place._seg_seg_dist((px, py), (vx, vy), (x1, y1), (x2, y2)) < stub / 2 + hw + clearance:
                return False
        for (x, y, r, net) in vias:
            d = math.hypot(vx - x, vy - y)
            if d < rv + r + (0.05 if net == gnd.GetNetCode() else clearance) + 0.1:
                return False
        return True

    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() != gnd.GetNetCode() or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            px, py = TO(p.GetPosition().x), TO(p.GetPosition().y)
            # already served by a via touching the pad?
            pbb = p.GetBoundingBox()
            r = (TO(pbb.GetX()), TO(pbb.GetY()), TO(pbb.GetRight()), TO(pbb.GetBottom()))
            if any(net == gnd.GetNetCode() and place._rect_point_dist(r, x, y) < 0.4 for (x, y, _, net) in vias):
                continue
            layer = pcbnew.B_Cu if p.IsOnLayer(pcbnew.B_Cu) else pcbnew.F_Cu
            hx, hy = (r[2] - r[0]) / 2, (r[3] - r[1]) / 2
            # prefer leaving along the pad's long axis, away from its footprint's centre
            cx, cy = TO(fp.GetPosition().x), TO(fp.GetPosition().y)
            dirs = [(1, 0), (-1, 0), (0, 1), (0, -1), (0.7071, 0.7071), (-0.7071, 0.7071), (0.7071, -0.7071), (-0.7071, -0.7071)]
            dirs.sort(key=lambda d: -((px - cx) * d[0] + (py - cy) * d[1]) - (0.3 if abs(d[0]) * hx > abs(d[1]) * hy else 0))
            placed = False
            for dist in [x * 0.1 for x in range(0, 16)]:
                for (dx, dy) in dirs:
                    reach = abs(dx) * hx + abs(dy) * hy
                    vx, vy = px + dx * (reach + rv + 0.12 + dist), py + dy * (reach + rv + 0.12 + dist)
                    if ok(vx, vy, px, py, layer, p):
                        t = pcbnew.PCB_TRACK(b)
                        t.SetStart(p.GetPosition()); t.SetEnd(V(vx, vy)); t.SetWidth(MM(stub)); t.SetLayer(layer); t.SetNet(gnd)
                        b.Add(t)
                        v = pcbnew.PCB_VIA(b)
                        v.SetPosition(V(vx, vy)); v.SetWidth(MM(via_d)); v.SetDrill(MM(drill)); v.SetNet(gnd)
                        b.Add(v)
                        vias.append((vx, vy, rv, gnd.GetNetCode()))
                        tracks.append((px, py, vx, vy, stub / 2, gnd.GetNetCode(), layer))
                        added += 1
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                print(f"  no ground via for {fp.GetReference()} pad {p.GetNumber()}")
    print(f"  {added} ground vias")


def escape_stubs(b, width=0.2, beyond=0.35):
    """Lead each signal pad inside a footprint's own keepout (the SHT45's)
    out through its notch. Freerouting keeps clearance from keepout edges,
    so it cannot fit a track into a 0.3 mm notch; it can reach the end of
    a stub that stops outside the keepout."""
    added = 0
    for fp in b.GetFootprints():
        kos = [z for z in fp.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowTracks()]
        if not kos:
            continue
        fx, fy = TO(fp.GetPosition().x), TO(fp.GetPosition().y)
        for p in fp.Pads():
            if not p.GetNetname() or p.GetNetname() == "GND":
                continue
            px, py = TO(p.GetPosition().x), TO(p.GetPosition().y)
            # straight out, away from the footprint's centre along its larger offset
            dx, dy = px - fx, py - fy
            ux, uy = ((1 if dx > 0 else -1), 0) if abs(dx) >= abs(dy) else (0, (1 if dy > 0 else -1))
            for step in range(1, 30):
                ex, ey = px + ux * 0.05 * step, py + uy * 0.05 * step
                seg = pcbnew.SEG(V(px, py), V(ex, ey))
                if any(z.Outline().Collide(seg, MM(width / 2)) for z in kos):
                    raise SystemExit(f"{fp.GetReference()} pad {p.GetNumber()}: no straight way out of the keepout")
                end = pcbnew.SEG(V(ex, ey), V(ex, ey))
                if all(not z.Outline().Collide(end, MM(width / 2 + beyond)) for z in kos):
                    break
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(p.GetPosition()); t.SetEnd(V(ex, ey)); t.SetWidth(MM(width))
            t.SetLayer(pcbnew.B_Cu if p.IsOnLayer(pcbnew.B_Cu) else pcbnew.F_Cu); t.SetNet(p.GetNet())
            b.Add(t)
            added += 1
    print(f"  {added} keepout escape stubs")


def remove_dangling(b):
    """Delete tracks with an end that touches nothing of their own net (an
    escape stub the router did not need, or a leftover of rip-up), by
    KiCad's own connectivity test, as its DRC sees them."""
    removed = 0
    while True:
        b.BuildConnectivity()
        conn = b.GetConnectivity()
        dead = [t for t in b.GetTracks() if not isinstance(t, pcbnew.PCB_VIA) and not t.IsLocked()
                and conn.TestTrackEndpointDangling(t, False)]
        if not dead:
            break
        for t in dead:
            b.Remove(t)
        removed += len(dead)
    print(f"  {removed} dangling tracks removed")


def fill(b):
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())


def autoroute(b, work: Path, passes=20, threads=0):
    """Route with Freerouting 1.9.0: every net but ground (-inc GND), no
    analytics (-da), and no optimiser pass (-mt 0): with In2 open to it,
    the optimiser ran past ten minutes whatever its threshold, and more
    than one thread makes clearance errors, as Freerouting warns.

    1.9.0, not 2.1.0: on this board 2.1.0 gave up on 79 connections where
    1.9.0 left 3, and 2.1.0's headless mode ignores -mp. 1.9.0 always opens
    its window, so it runs under a virtual display where there is none;
    given -de and -do it routes, saves the session and exits."""
    work = work.resolve()
    dsn, ses = work / "hub.dsn", work / "hub.ses"
    ses.unlink(missing_ok=True)
    ok = pcbnew.ExportSpecctraDSN(b, str(dsn))
    if not ok:
        raise SystemExit("DSN export failed")
    # Freerouting keeps its settings in the temp directory and its log in
    # the working directory; give it its own of both, so a run depends on
    # nothing left by an earlier one.
    fr = work / "freerouting"
    fr.mkdir(exist_ok=True)
    cmd = ["java", f"-Djava.io.tmpdir={fr}", "-jar", str(JAR.resolve()), "-de", str(dsn), "-do", str(ses),
           "-mp", str(passes), "-mt", str(threads), "-inc", "GND", "-da"]
    if not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a"] + cmd
    print(" ".join(cmd), flush=True)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, cwd=fr)
    tail = [l for l in (res.stdout + res.stderr).splitlines() if "JAVA_TOOL" not in l][-25:]
    print("\n".join(tail))
    if not ses.exists():
        raise SystemExit("Freerouting produced no session file")
    if not pcbnew.ImportSpecctraSES(b, str(ses)):
        raise SystemExit("SES import failed")


if __name__ == "__main__":
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "sideline-cam-hub.kicad_pcb"
    b = pcbnew.LoadBoard(str(src))
    bb = b.GetBoardEdgesBoundingBox()
    frame = (TO(bb.GetX()), TO(bb.GetY()), TO(bb.GetRight()), TO(bb.GetBottom()))
    planes(b, frame)
    fanout_gnd(b)
    escape_stubs(b)
    fill(b)
    work = OUT / "route"
    work.mkdir(exist_ok=True)
    autoroute(b, work)
    remove_dangling(b)
    pours(b, frame)
    fill(b)
    pcbnew.SaveBoard(str(src), b)
    print("routed", src)
